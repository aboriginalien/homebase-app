"""Homebase: local SIWC registration and one plan-backed Responses proof.

No API-key input, remote callbacks, browser token storage, or Codex auth import.
"""
import argparse
import base64
import contextlib
import fcntl
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import stat
import sys
import tempfile
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt
import requests

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
APP_NAME = "Homebase"
MODEL = "gpt-6.1-sol"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPES = {"offline_access", "resource.invoke", "chatgpt.tokens.use.direct"}
USAGE_URL = "https://chatgpt.com/settings/usage"
SOURCE = Path(__file__).resolve().parent
TOKEN_FIELDS = ("access_token", "refresh_token", "id_token")


class ProbeError(Exception):
    """Only fixed, credential-free diagnostic messages cross the CLI boundary."""


def default_data_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/HomebaseProbe"
    return Path.home() / ".local/share/homebase-probe"


def check_private(path, directory=False):
    s = path.lstat()
    expected = stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode)
    if (not expected or s.st_uid != os.getuid() or s.st_mode & 0o077
            or (not directory and s.st_nlink != 1)):
        raise ProbeError("Local storage must be owner-only, with no symlinks/hardlinks.")


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser()
        if self.directory.resolve().is_relative_to(SOURCE):
            raise ProbeError("Credential storage must be outside the source tree.")
        if not self.directory.exists() and not self.directory.is_symlink():
            self.directory.mkdir(mode=0o700, parents=True)
        check_private(self.directory, directory=True)
        self.path = self.directory / "session.json"

    @contextlib.contextmanager
    def locked(self):
        p = self.directory / "session.lock"
        fd = os.open(p, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            check_private(p)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ProbeError("Another Homebase operation is running; retry when it finishes.")
            yield
        finally:
            os.close(fd)

    def load(self):
        if self.path.exists() or self.path.is_symlink():
            check_private(self.path)
            try:
                d = json.loads(self.path.read_text())
                if d["version"] != 1 or not isinstance(d["accounts"], dict):
                    raise ValueError()
                if not d["host_id"].startswith("urn:uuid:"):
                    raise ValueError()
                uuid.UUID(d["host_id"][9:])
                return d
            except (ValueError, KeyError, TypeError):
                raise ProbeError("Local storage is invalid; preserve it before troubleshooting.")
        d = {"version": 1, "host_id": "urn:uuid:" + str(uuid.uuid4()),
             "accounts": {}, "active": None}
        self.save(d)
        return d

    def save(self, data):
        if self.path.exists() or self.path.is_symlink():
            check_private(self.path)
        fd, name = tempfile.mkstemp(prefix=".session-", dir=self.directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.path)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)


class NoImplicitAuth(requests.auth.AuthBase):
    def __call__(self, request):
        return request  # Preserve supported proxies; suppress implicit .netrc auth.


class HTTP:
    def __init__(self):
        self.session = requests.Session()
        self.session.auth = NoImplicitAuth()

    def request(self, method, url, **kwargs):
        try:
            r = self.session.request(method, url, timeout=(10, 60),
                                     allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise ProbeError("Network/TLS request failed; no success was confirmed.") from None
        if not 200 <= r.status_code < 300:
            status = r.status_code
            r.close()
            raise ProbeError(f"Provider request failed (HTTP {status}); retry sign-in if authorization expired.")
        return r

    def json(self, method, url, **kwargs):
        with self.request(method, url, **kwargs) as r:
            try:
                data = r.json()
            except ValueError:
                raise ProbeError("Provider returned invalid JSON.") from None
            if not isinstance(data, dict):
                raise ProbeError("Provider returned an invalid object.")
            return data


def discovery(http):
    d = http.json("GET", ISSUER + "/.well-known/openid-configuration")
    if d.get("issuer") != ISSUER or "RS256" not in d.get("id_token_signing_alg_values_supported", []):
        raise ProbeError("OIDC discovery issuer/signing algorithm is invalid.")
    for field in ("authorization_endpoint", "token_endpoint", "jwks_uri", "revocation_endpoint"):
        u = urlsplit(d.get(field, ""))
        if (u.scheme != "https" or u.hostname != "auth.openai.com" or u.port not in (None, 443)
                or u.username or u.password or u.query or u.fragment):
            raise ProbeError("OIDC discovery endpoint is invalid.")
    return d


def pending_attempt(host_id, port, account=None):
    verifier = secrets.token_urlsafe(48)
    p = {"state": secrets.token_urlsafe(32), "nonce": secrets.token_urlsafe(32),
         "verifier": verifier, "redirect_uri": f"http://127.0.0.1:{port}/auth/callback",
         "client_id": account["client_id"] if account else "dynamic_agent_client",
         "expires_at": time.time() + 300, "host_id": host_id}
    p["challenge"] = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return p


def authorization_url(d, p, account=None):
    q = {"client_id": p["client_id"], "ext_agent_host_id": p["host_id"],
         "redirect_uri": p["redirect_uri"], "response_type": "code", "scope": SCOPES,
         "resource": RESOURCE, "state": p["state"], "nonce": p["nonce"],
         "code_challenge": p["challenge"], "code_challenge_method": "S256"}
    if p["client_id"] == "dynamic_agent_client":
        q["agent_name_hint"] = APP_NAME
    elif account and account.get("id_token"):
        q["id_token_hint"] = account["id_token"]
    return d["authorization_endpoint"] + "?" + urlencode(q)


def callback(target, host_header, p):
    u = urlsplit(target)
    expected = urlsplit(p["redirect_uri"])
    if (u.scheme or u.netloc or u.fragment or u.path != expected.path
            or host_header != expected.netloc or time.time() >= p["expires_at"]):
        raise ProbeError("Wrong or expired callback.")
    q = parse_qs(u.query, keep_blank_values=True)
    if any(len(v) != 1 for v in q.values()):
        raise ProbeError("Duplicate callback parameter.")
    if not hmac.compare_digest(q.get("state", [""])[0], p["state"]):
        raise ProbeError("Callback state did not match.")
    if "error" in q:
        raise ProbeError("Sign-in or plan consent was declined; no session saved.")
    client = q.get("client_id", [p["client_id"]])[0]
    if (not client.startswith("oaiapp_") or
            (p["client_id"] != "dynamic_agent_client" and client != p["client_id"])):
        raise ProbeError("Callback did not return the expected issued client ID.")
    code = q.get("code", [""])[0]
    if not code or len(code) > 8192:
        raise ProbeError("Callback authorization code is missing or invalid.")
    return code, client


def identity(token, jwks, client_id, nonce=None, subject=None):
    try:
        h = jwt.get_unverified_header(token)
        if h.get("alg") != "RS256" or not h.get("kid"):
            raise ValueError()
        keys = [k for k in jwks["keys"] if k.get("kid") == h["kid"]
                and k.get("use", "sig") == "sig" and k.get("alg", "RS256") == "RS256"]
        if len(keys) != 1:
            raise ValueError()
        key = jwt.PyJWK.from_dict(keys[0], algorithm="RS256").key
        required = ["iss", "aud", "sub", "iat", "exp"] + (["nonce"] if nonce is not None else [])
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=client_id,
                            issuer=ISSUER, options={"require": required})
        if not isinstance(claims["sub"], str) or not claims["sub"]:
            raise ValueError()
        aud = claims["aud"]
        if ((isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != client_id)
                or ("azp" in claims and claims["azp"] != client_id)):
            raise ValueError()
        if nonce is not None and not hmac.compare_digest(claims["nonce"], nonce):
            raise ValueError()
        if subject is not None and claims["sub"] != subject:
            raise ValueError()
        return claims
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        raise ProbeError("ID-token signature/issuer/audience/time/nonce/account validation failed.") from None


def token_record(tokens, client_id, claims, prior=None):
    prior = prior or {}
    scope = tokens.get("scope")
    scopes = scope.split() if isinstance(scope, str) else prior.get("scopes", [])
    if not PLAN_SCOPES.issubset(scopes):
        raise ProbeError("Required ChatGPT-plan permission was not granted; no inference allowed.")
    expiry = tokens.get("expires_in")
    if (str(tokens.get("token_type", "")).lower() != "bearer" or
            not isinstance(expiry, int) or isinstance(expiry, bool) or not 0 < expiry <= 604800):
        raise ProbeError("Token response type/expiry is invalid.")
    fields = {f: tokens.get(f, prior.get(f)) for f in TOKEN_FIELDS}
    if not all(isinstance(v, str) and v for v in fields.values()):
        raise ProbeError("Protected token set is incomplete.")
    return {"client_id": client_id, "issuer": ISSUER, "subject": claims["sub"],
            "scopes": scopes, "expires_at": time.time() + expiry, **fields}


def register(http, d, p, code, client_id, prior=None):
    t = http.json("POST", d["token_endpoint"], data={"grant_type": "authorization_code",
                 "client_id": client_id, "code": code, "code_verifier": p["verifier"],
                 "redirect_uri": p["redirect_uri"], "resource": RESOURCE})
    jwks = http.json("GET", d["jwks_uri"])
    claims = identity(t.get("id_token", ""), jwks, client_id, p["nonce"],
                      prior["subject"] if prior else None)
    return token_record(t, client_id, claims)


def select_account(data, account_id=None):
    key = account_id or data.get("active")
    if not key or key not in data["accounts"]:
        raise ProbeError("Choose a saved account or run login first.")
    return key, data["accounts"][key]


def account_key(record):
    return hashlib.sha256((record["issuer"] + "|" + record["subject"] + "|" + record["client_id"]).encode()).hexdigest()[:16]


HANDOFF_LIMIT = 262144
RECORD_FIELDS = {"client_id", "issuer", "subject", "scopes", "expires_at", *TOKEN_FIELDS}


def host_identifier(value):
    try:
        if not isinstance(value, str) or not value.startswith("urn:uuid:"):
            raise ValueError()
        parsed = uuid.UUID(value[9:])
        if parsed.version != 4 or str(parsed) != value[9:]:
            raise ValueError()
        return value
    except (ValueError, AttributeError):
        raise ProbeError("Handoff requires a valid distinct host identifier.") from None


def handoff_record(record, key, expected_client):
    """Offline shape/claim consistency only; NOT signature or live grant validation.

    Only an already-validated Homebase registration carried over a trusted channel
    is an acceptable source. This is not a generic OAuth/Codex-cache importer.
    """
    try:
        if not isinstance(record, dict) or set(record) != RECORD_FIELDS:
            raise ValueError()
        client = record["client_id"]
        if (not isinstance(client, str) or not client.startswith("oaiapp_")
                or len(client) > 256 or client != expected_client
                or record["issuer"] != ISSUER
                or not isinstance(record["subject"], str) or not 0 < len(record["subject"]) <= 512
                or account_key(record) != key):
            raise ValueError()
        scopes = record["scopes"]
        if (not isinstance(scopes, list) or len(scopes) > 32
                or not all(isinstance(s, str) and 0 < len(s) <= 128 for s in scopes)
                or len(set(scopes)) != len(scopes) or not PLAN_SCOPES.issubset(scopes)):
            raise ValueError()
        expiry = record["expires_at"]
        if (isinstance(expiry, bool) or not isinstance(expiry, (int, float))
                or not math.isfinite(expiry) or not 0 < expiry <= time.time() + 604800):
            raise ValueError()
        if not all(isinstance(record[f], str) and 0 < len(record[f]) <= 65536
                   and not any(ord(c) < 32 or ord(c) == 127 for c in record[f]) for f in TOKEN_FIELDS):
            raise ValueError()
        if any(not 33 <= ord(c) <= 126 for c in record["access_token"]):
            raise ValueError()  # Bearer header must not trigger token-bearing encoding errors.
        header = jwt.get_unverified_header(record["id_token"])
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or not header["kid"]:
            raise ValueError()
        claims = jwt.decode(record["id_token"], options={"verify_signature": False})
        aud = claims.get("aud")
        matching_aud = (aud == client or
                        (isinstance(aud, list) and 0 < len(aud) <= 16
                         and all(isinstance(v, str) and v for v in aud)
                         and len(set(aud)) == len(aud) and client in aud))
        if (claims.get("iss") != ISSUER or claims.get("sub") != record["subject"]
                or not matching_aud
                or (isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != client)
                or ("azp" in claims and claims["azp"] != client)):
            raise ValueError()
        # A retained ID token can be expired after refresh; do not pretend to
        # authenticate it here. Preserve it as metadata for later reauthorization.
        for name in ("iat", "exp"):
            value = claims[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError()
        if not 0 < claims["iat"] <= time.time() + 300 or claims["exp"] <= claims["iat"]:
            raise ValueError()
        return record
    except (ValueError, TypeError, KeyError, jwt.PyJWTError):
        raise ProbeError("Handoff registration is malformed or does not match the selected account/client.") from None


def handoff_path(path, store):
    path = Path(path).expanduser().absolute()
    if (path != path.resolve() or path.resolve().is_relative_to(SOURCE)
            or path in (store.path.absolute(), (store.directory / "session.lock").absolute())
            or path.name.startswith(".session-")):
        raise ProbeError("Handoff path must be outside source and separate from protected session files.")
    check_private(path.parent, directory=True)
    if store.directory.absolute() != store.directory.resolve():
        raise ProbeError("Handoff storage cannot traverse symlinks.")
    check_private(store.directory, directory=True)
    return path


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_handoff(path, payload):
    """Atomic new-file publication; never replace an existing destination."""
    fd, name = tempfile.mkstemp(prefix=".handoff-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        os.link(name, path, follow_symlinks=False)  # EEXIST rather than overwrite.
        os.unlink(name)
        sync_directory(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def unique_object(pairs):
    obj = {}
    for name, value in pairs:
        if name in obj:
            raise ValueError()
        obj[name] = value
    return obj


def read_handoff(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > HANDOFF_LIMIT):
            raise ProbeError("Handoff input must be a bounded owner-only regular file with no links.")
        raw = source.read(HANDOFF_LIMIT + 1)
    if len(raw) > HANDOFF_LIMIT:
        raise ProbeError("Handoff input exceeds its safe size bound.")
    return json.loads(raw, object_pairs_hook=unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def export_registration(store, data, key, destination, target_host):
    """Freeze helper use BEFORE creating a transferable file, then clear tokens.

    Crash/failure leaves the helper frozen; never silently restore refresh ownership.
    Call with the existing Store lock. No network or plaintext output.
    """
    try:
        if not key:
            raise ProbeError("Select an explicit saved account for handoff.")
        key, account = select_account(data, key)
        target = host_identifier(target_host)
        source = host_identifier(data["host_id"])
        if target == source:
            raise ProbeError("Helper and VM host identifiers must differ.")
        path = handoff_path(destination, store)
        pending = account.get("handoff")
        if pending and pending != {"phase": "frozen", "target_host_id": target}:
            raise ProbeError("Account is already handed off or frozen for another target.")
        record = {name: value for name, value in account.items() if name != "handoff"}
        handoff_record(record, key, record.get("client_id"))
        document = {"version": 1, "kind": "homebase-siwc-registration",
                    "source_host_id": source, "target_host_id": target,
                    "account": key, "registration": record}
        payload = json.dumps(document, allow_nan=False).encode()
        if len(payload) > HANDOFF_LIMIT:
            raise ProbeError("Handoff input exceeds its safe size bound.")
        if path.exists() or path.is_symlink():
            if not pending or read_handoff(path) != document:
                raise ProbeError("Handoff output already exists or differs; no file was overwritten.")
            # Recovery after a final source-save failure: consume the identical
            # protected export in place, never unfreeze or replace its bytes.
        else:
            account["handoff"] = {"phase": "frozen", "target_host_id": target}
            store.save(data)  # Durable freeze BEFORE any export can be transferred.
            write_handoff(path, payload)
        for name in TOKEN_FIELDS:
            account.pop(name, None)
        account["scopes"] = []
        account["expires_at"] = 0
        account["handoff"]["phase"] = "exported"
        store.save(data)
    except (OSError, ValueError, TypeError, KeyError):
        raise ProbeError("Handoff export failed; preserve private state/files and keep helper use stopped.") from None


def import_registration(store, data, key, source_path, expected_client):
    """Import one protected record without changing VM host or unrelated sessions.

    Offline consistency checks trust the protected source/channel, not JWT signature.
    Call with the existing Store lock. No network or plaintext output.
    """
    try:
        if not key:
            raise ProbeError("Select the expected saved account for handoff.")
        path = handoff_path(source_path, store)
        document = read_handoff(path)
        fields = {"version", "kind", "source_host_id", "target_host_id", "account", "registration"}
        if (not isinstance(document, dict) or set(document) != fields
                or type(document["version"]) is not int or document["version"] != 1
                or document["kind"] != "homebase-siwc-registration"
                or document["account"] != key):
            raise ProbeError("Handoff document does not match the expected format/account.")
        source = host_identifier(document["source_host_id"])
        target = host_identifier(document["target_host_id"])
        if target != host_identifier(data["host_id"]) or target == source:
            raise ProbeError("Handoff is bound to a different VM host.")
        record = handoff_record(document["registration"], key, expected_client)
        if key in data["accounts"]:
            raise ProbeError("VM account already exists; no session was replaced.")
        updated = {**data, "accounts": {**data["accounts"], key: record},
                   "active": data.get("active") or key}
        store.save(updated)  # Atomic local commit; unrelated active account retained.
        path.unlink()  # Consume only AFTER successful commit.
        sync_directory(path.parent)
    except (OSError, ValueError, TypeError, KeyError):
        raise ProbeError("Handoff import failed; inspect protected VM state before retry; no success confirmed.") from None


def require_local_owner(account):
    if account and account.get("handoff"):
        raise ProbeError("Account is handed off/frozen; helper login, refresh and revocation are blocked.")


def renew(http, d, store, data, key):
    a = data["accounts"][key]
    require_local_owner(a)
    if not PLAN_SCOPES.issubset(a.get("scopes", [])) or not all(a.get(f) for f in TOKEN_FIELDS):
        raise ProbeError("Signed out or missing plan permission; run login.")
    if time.time() < a["expires_at"] - 60:
        return a
    t = http.json("POST", d["token_endpoint"], data={"grant_type": "refresh_token",
                 "client_id": a["client_id"], "refresh_token": a["refresh_token"], "resource": RESOURCE})
    # Scope omitted in refresh responses means unchanged grant (OAuth 2.0).
    claims = {"sub": a["subject"]}
    if t.get("id_token"):
        claims = identity(t["id_token"], http.json("GET", d["jwks_uri"]),
                          a["client_id"], subject=a["subject"])
    updated = token_record(t, a["client_id"], claims, prior=a)
    data["accounts"][key] = updated
    store.save(data)  # Latest rotating refresh token and access/expiry saved together.
    return updated


def signout(http, d, store, data, key):
    a = data["accounts"][key]
    require_local_owner(a)
    confirmed = not a.get("refresh_token")
    for attempt in range(3):
        if confirmed:
            break
        try:
            with http.request("POST", d["revocation_endpoint"], data={
                    "token": a["refresh_token"], "token_type_hint": "refresh_token",
                    "client_id": a["client_id"]}) as r:
                confirmed = r.status_code == 200
        except ProbeError:
            if attempt < 2:
                time.sleep(0.5 * (2 ** attempt))
    for field in TOKEN_FIELDS:
        a.pop(field, None)
    a["scopes"] = []
    a["expires_at"] = 0
    store.save(data)
    return confirmed


def sse_events(lines):
    chunks = []
    for line in lines:
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        if line == "":
            if chunks:
                text = "\n".join(chunks)
                if text == "[DONE]":
                    return
                try:
                    event = json.loads(text)
                except ValueError:
                    raise ProbeError("Malformed response stream; reply is incomplete.") from None
                if not isinstance(event, dict):
                    raise ProbeError("Malformed response stream; reply is incomplete.")
                yield event
                chunks = []
        elif line.startswith("data:"):
            chunks.append(line[5:].lstrip(" "))
            if sum(map(len, chunks)) > 1_000_000:
                raise ProbeError("Response event exceeded the probe limit.")
    if chunks:
        raise ProbeError("Interrupted response event; reply is incomplete.")


def choose_model(catalog):
    models = catalog.get("models")
    if not isinstance(models, list):
        raise ProbeError("Account model catalog contract is unavailable.")
    choices = [m for m in models if m.get("visibility") == "list" and
               (m.get("slug") == MODEL or m.get("display_name", "").casefold() == "gpt-6.1 sol")]
    exact = [m for m in choices if m.get("slug") == MODEL]
    if len(exact) == 1:
        return exact[0]["slug"]
    if len(choices) == 1 and isinstance(choices[0].get("slug"), str) and choices[0]["slug"]:
        return choices[0]["slug"]
    raise ProbeError("GPT-6.1 Sol is unavailable or ambiguous for this account; no substitute selected.")


def consume(events, model, emit):
    text = ""
    for e in events:
        kind = e.get("type")
        if kind == "response.output_text.delta":
            delta = e.get("delta")
            if not isinstance(delta, str) or len(text) + len(delta) > 16384:
                raise ProbeError("Invalid/oversized text stream; reply is incomplete.")
            text += delta
            emit(delta)
        elif kind in ("response.failed", "error", "response.incomplete"):
            raise ProbeError("Inference failed or incomplete; check authorization and ChatGPT usage settings.")
        elif kind == "response.completed":
            r = e.get("response", {})
            if r.get("status") != "completed":
                raise ProbeError("Stream terminal status was not completed.")
            if (r.get("model") != model or r.get("reasoning", {}).get("effort") != "high"
                    or r.get("service_tier") != "default"):
                raise ProbeError("Response completed, but target model/high/standard settings were not verified.")
            if text.strip() != "Hello, world!":
                raise ProbeError("Response completed, but the hello-world functional check did not match.")
            return {"completed": True, "model": model, "reasoning": "high", "service_tier": "default"}
    raise ProbeError("Stream ended without response.completed; reply is incomplete.")


def probe(http, account, emit):
    headers = {"Authorization": "Bearer " + account["access_token"]}
    model = choose_model(http.json("GET", RESOURCE + "/models", headers=headers))
    payload = {"model": model, "input": [{"role": "user", "content": "Say exactly: Hello, world!"}],
               "store": False, "stream": True, "reasoning": {"effort": "high"}, "service_tier": "default"}
    with http.request("POST", RESOURCE + "/responses", headers=headers, json=payload, stream=True) as r:
        return consume(sse_events(r.iter_lines()), model, emit)


def local_login(http, d, host_id, account=None):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *args):
            pass  # Never log callback query/code/state or authorization URLs.

        def do_GET(self):
            try:
                if len(self.path) > 16384:
                    raise ProbeError("Callback is too large.")
                if self.headers.get("Origin") or self.headers.get_all("Host") != [expected_host]:
                    raise ProbeError("Callback host/origin is invalid.")
                result = callback(self.path, self.headers.get("Host"), p)
            except (ProbeError, ValueError) as error:
                # Wrong-state/unrelated requests cannot consume the valid pending attempt.
                self.send_response(400)
                body = b"Homebase could not validate this callback. Retry from the local client."
                if isinstance(error, ProbeError) and "declined" in str(error):
                    server.failure = error
            else:
                server.result = result
                self.send_response(200)
                body = b"Homebase received the callback. Return to the local client for validation."
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = 1
        server.result = None
        server.failure = None
        expected_host = f"127.0.0.1:{server.server_port}"
        p = pending_attempt(host_id, server.server_port, account)
        print("Continue with ChatGPT in this computer's browser. Authorization URL is not logged.")
        if not webbrowser.open(authorization_url(d, p, account)):
            raise ProbeError("Local browser could not open. Run login on the helper computer with a browser.")
        while not server.result and not server.failure and time.time() < p["expires_at"]:
            server.handle_request()
        if server.failure:
            raise server.failure
        if not server.result:
            raise ProbeError("Local sign-in timed out; no session saved.")
        code, client_id = server.result
        return register(http, d, p, code, client_id, account)


def run(argv=None):
    parser = argparse.ArgumentParser(description="Local Homebase ChatGPT-plan proof; no API-key fallback.")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir(), help="Owner-only storage outside source")
    parser.add_argument("--account", help="Saved registration label from status")
    commands = parser.add_subparsers(dest="command", required=True)
    login = commands.add_parser("login", help="Continue with ChatGPT using this computer's browser")
    login.add_argument("--new-account", action="store_true", help="Keep existing registrations; add a distinct one")
    for command in ("init", "status", "probe", "signout"):
        commands.add_parser(command)
    export = commands.add_parser("export", help="Offline selected-registration handoff; freezes helper use")
    export.add_argument("--file", type=Path, required=True, help="New owner-only file outside source")
    export.add_argument("--target-host-id", required=True, help="Already initialized destination VM host ID")
    importer = commands.add_parser("import", help="Offline protected import; never proves entitlement")
    importer.add_argument("--file", type=Path, required=True, help="Protected transferred file; consumed on success")
    importer.add_argument("--expected-client-id", required=True, help="Expected issued SIWC client, not a token")
    args = parser.parse_args(argv)
    store = Store(args.data_dir)
    with store.locked():
        if args.command == "import" and not store.path.exists():
            raise ProbeError("Initialize the VM host first; import cannot invent a receiving host.")
        data = store.load()
        if args.command in ("init", "status"):
            print("Local host initialized. This is not provider authentication.")
            for key, a in data["accounts"].items():
                print(key, "active" if key == data["active"] else "saved",
                      "handed off/frozen; helper use blocked" if a.get("handoff") else
                      "local plan grant present (not revalidated)" if a.get("access_token") else "signed out")
            return 0
        if args.command == "export":
            export_registration(store, data, args.account, args.file, args.target_host_id)
            print("Protected handoff prepared. Helper session frozen/cleared; no transfer or inference verified.")
            return 0
        if args.command == "import":
            import_registration(store, data, args.account, args.file, args.expected_client_id)
            print("Protected registration imported; input consumed. VM owns refresh; entitlement/inference NOT verified.")
            return 0
        if args.command != "login" or args.account or (data.get("active") and not args.new_account):
            _, selected = select_account(data, args.account)
            require_local_owner(selected)  # Before discovery or signout's cleanup handler.
        http = HTTP()
        if args.command == "signout":
            key, a = select_account(data, args.account)
            try:
                d = discovery(http)
                confirmed = signout(http, d, store, data, key)
            except ProbeError:
                for f in TOKEN_FIELDS:
                    a.pop(f, None)
                a["scopes"] = []
                a["expires_at"] = 0
                store.save(data)
                confirmed = False
            print("Signed out locally. " + ("Remote revocation confirmed." if confirmed else
                  "Remote revocation unconfirmed; disconnect Homebase in ChatGPT settings."))
            return 0 if confirmed else 2
        d = discovery(http)
        if args.command == "login":
            prior = None
            if args.account or (data.get("active") and not args.new_account):
                _, prior = select_account(data, args.account)
            if args.new_account and args.account:
                raise ProbeError("Choose a saved registration or a new account, not both.")
            a = local_login(http, d, data["host_id"], prior)
            key = account_key(a)
            data["accounts"][key] = a
            data["active"] = key
            store.save(data)
            print("You're using your ChatGPT plan. Permission saved; inference is not yet verified.")
            print("Manage usage:", USAGE_URL)
            return 0
        key, _ = select_account(data, args.account)
        a = renew(http, d, store, data, key)
        print("Using ChatGPT plan · target GPT-6.1 Sol / high / standard. Manage usage:", USAGE_URL)
        result = probe(http, a, lambda delta: print(delta, end="", flush=True))
        print("\nVerified completed response:", json.dumps(result))
        return 0


def main():
    try:
        return run()
    except ProbeError as error:
        print("Homebase:", error, file=sys.stderr)
        return 1
    except OSError:
        print("Homebase: Protected local storage operation failed; preserve state before retry.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Homebase: stopped; completion was not confirmed.", file=sys.stderr)
        return 130
    except Exception:
        print("Homebase: local operation failed; no success is claimed. Do not share credential files.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
