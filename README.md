# Homebase — local ChatGPT-plan proof

A small open-source command-line client that registers Homebase through the
documented Sign in with ChatGPT (SIWC) flow and requests exactly one streamed
hello-world response. No chat UI, threads, memory, tools, hosting or API-key
fallback. MIT applies to original code; dependencies retain their own licenses.

**Installed code and offline tests are not proof of subscription inference.**
A live proof requires eligible account consent, actual plan grants, model access
and `response.completed` with the requested settings. A staged source package is
not a published open-source release.

## Local setup

Use CPython **3.12** on Linux x86_64 or an Intel Mac with macOS 11 or later.
The locked wheels were resolved for these platforms; Mac runtime execution must
be verified on the actual computer. No Codex desktop app, Rust or source build
is required. Do not use the old system Python supplied with macOS.

Install Python 3.12 from its official distribution if needed:
https://www.python.org/downloads/release/python-31210/ . Check the installer's
actual OS requirements before installation. Use its bundled **Install
Certificates.command** when required; never disable TLS verification. A newer
maintained 3.12 build is also possible, but verify OS compatibility and wheel
installation on that computer. Availability of a wheel is not a tested Mac run.

From this code-only directory:

```sh
python3.12 -m venv "$HOME/.local/share/homebase-probe-env"
"$HOME/.local/share/homebase-probe-env/bin/python" -m pip install --require-hashes --only-binary=:all: -r requirements.txt
"$HOME/.local/share/homebase-probe-env/bin/python" -m unittest discover -s tests -v
"$HOME/.local/share/homebase-probe-env/bin/python" homebase_probe.py login
"$HOME/.local/share/homebase-probe-env/bin/python" homebase_probe.py probe
```

Alternatively, open **run-probe.command** on the local helper. It checks Python,
installs the hash-locked dependencies outside source, runs offline tests, then
asks before opening the official sign-in and requesting one response. If Finder
does not allow launching the downloaded file, run `bash run-probe.command` from
Terminal inside this folder; do not remove OS protections blindly. `bash
run-probe.command --check` runs only installation/tests/local status: no login,
model catalog or inference. `HOMEBASE_PYTHON` can select a Python 3.12 executable;
`HOMEBASE_DATA_DIR` can select owner-only storage outside source.

## What login does

It binds a real listener to **127.0.0.1** before opening this computer's browser.
Its callback is `/auth/callback`; only the available port varies. A browser on
another device, including an iPad, cannot reach this computer's listener at its
own loopback address. If operating a helper remotely, open its browser on that
helper's desktop, not in the controlling tablet browser.

The initial public client uses `dynamic_agent_client`, app name **Homebase**, a
persistent opaque host ID, fresh state/nonce/PKCE S256 and the documented identity
and plan scopes. It saves the issued client ID only after signature, issuer,
audience, time, nonce, account and plan-grant validation. Returning login uses
that saved ID and checks the same account; `login --new-account` adds another
registration without overwriting others. `status` shows distinct local labels;
`--account LABEL` selects a saved registration. It never imports Codex task auth.

## One response, no substitution

`probe` queries the selected account's model catalog with its SIWC OAuth access
token and chooses the returned GPT-6.1 Sol slug. If unavailable or ambiguous,
it stops. The fixed request is `Say exactly: Hello, world!`, with `store:false`,
`stream:true`, `reasoning:{effort:"high"}`, and `service_tier:"default"` (standard;
no Fast/priority opt-in). It requires a completed terminal event and returned
model/reasoning/tier evidence. Missing/different settings fail with an explicit
limitation; there is no silent fallback. A completed but nonmatching hello-world
reply also fails the functional check.

During inference, **Using ChatGPT plan** is shown with **Manage usage**:
https://chatgpt.com/settings/usage . This uses existing eligible plan limits,
not an unlimited allowance. Failed/quota/incomplete/interrupted streams exit
nonzero and mark success unconfirmed. Partial text can be visible; it is never
reported as a completed proof. The probe does not store the transcript.

## Protected sessions and sign-out

Default state is outside source: `~/Library/Application Support/HomebaseProbe`
on macOS, or `~/.local/share/homebase-probe` on Linux. The directory is owner-only
0700; `session.json` is atomic/0600. A POSIX file lock serializes operations and
rotating refresh. Each verified account/client pair has a separate record;
active account selection is explicit. Refresh occurs near expiry, preserving
the issued client ID and replacing access/refresh/expiry together. Invalid
renewal stops the proof and leaves the prior state for a fresh sign-in; it does
not fall back to another account or API credentials.

`signout` attempts documented refresh-token revocation, then clears local access,
refresh and ID tokens even when network discovery/revocation fails. It retains
the opaque host and account/client mapping. Unconfirmed revocation exits 2 and
directs you to disconnect Homebase in ChatGPT settings. Local sign-out blocks
further inference until new validated authorization. `status` describes local
state only; it does not verify current server entitlement.

Never share session files, tokens, authorization URLs, callback URLs, screenshots
of credentials or browser persistent storage. Callback HTTP logs and raw provider
errors are suppressed. Tokens are used only for the documented HTTPS endpoints;
redirects on credential requests are rejected. Standard proxy/TLS verification
remains enabled; implicit `.netrc` credentials are disabled. Keep the data
directory and any backups private. No live credential examples are included.

## Later self-hosted VM use

First prove this same qualifying client locally. The official VM route creates
a **distinct VM host ID**, completes OAuth locally with the same tool/client,
user and workspace, then securely transfers the selected protected registration
to its VM storage, preserving the VM host ID and letting the VM own refresh.
This probe's `init` prepares a fresh host. The **offline** `export` and `import`
commands below implement selected-registration files; they do not perform network
transfer, deployment or remote web access. Do not copy the entire local state over
a VM's host ID or share a refresh-token session between simultaneously refreshing
processes. Credential transfer must be a separate authorized operation.

### Offline selected-registration handoff

Use these only after real local consent/proof, with a verified personal VM target
and a separately authorized secure-channel operation. This is **not phone-only
onboarding**. Native Codex device-code tokens/auth caches are not SIWC credentials
for this importer. Native app-server authentication is not permitted for hosted
services; replacing it with an arbitrary public callback is not supported.

The following uppercase arguments are metadata/path placeholders resolved by the
protected operator runtime, not values to paste literally or copy from a report.
`ACCOUNT_LABEL` is the exact selected label from the helper's status; expected
issued client and already-persisted VM host ID must match the selected registration.
Never pass tokens in command arguments. Use this code revision on both hosts,
the already prepared venv on the VM, and an initialized private VM state directory.
Export/inbox parent directories must already exist with owner-only 0700 permissions
outside source; files are 0600. Do not use a public temp directory as the parent.

```sh
python homebase_probe.py --data-dir HELPER_STATE --account ACCOUNT_LABEL export --file PRIVATE_EXPORT_DIR/registration.handoff.json --target-host-id VM_HOST_ID
# A separately authorized pinned SSH channel copies only this protected file.
python homebase_probe.py --data-dir VM_STATE --account ACCOUNT_LABEL import --file PRIVATE_VM_INBOX/registration.handoff.json --expected-client-id ISSUED_CLIENT_ID
```

Export selects only that account, validates the existing Homebase record, durably
freezes helper use **before** publishing a new atomic file, then clears helper
tokens without provider revocation. The original host and unrelated registrations
are retained. Login/refresh/probe/signout of the frozen account are blocked before
provider requests; signout must not accidentally revoke the transferred VM session.
The export has only the selected record plus source/target host binding metadata.
It refuses existing files, except an exactly matching protected file when recovering
a previously frozen export whose final helper-state write failed.
Quiesce all helper processes first and use this revision on both hosts. Do not
run an older version that ignores the freeze marker: after a failed final write,
the frozen helper record can still contain protected tokens pending cleanup.

Import requires that the VM was initialized beforehand, explicit account/client
metadata, exact bound VM host ID and a protected regular file. It rejects unknown
formats/fields, native caches, duplicate JSON keys, oversized input, malformed or
mismatched identity/token/grant metadata, links, unsafe modes and path collisions.
It never replaces an existing registration. The atomic state addition preserves
VM host ID and all unrelated records/active selection; on an empty VM it selects
the imported account. The VM input file is removed only after state commit.

**Offline checks do not validate JWT signatures or server entitlement.** They
check shape and consistency of the retained ID token against the previously
validated Homebase record, including issuer/subject/audience/client. The trusted
helper and pinned secure channel supply provenance. An expired retained ID token
or expired access metadata is allowed for the documented refresh/reauthorization
lifecycle; actual refresh/inference must subsequently succeed. Never treat an
imported file, granted-scope strings or a model list alone as proof of plan access.

On any failure, preserve protected state/files and keep helper use stopped. Before
an export-file write failure, helper may already be durably frozen; retry the same
account/target once the storage error is resolved. If the final helper write failed
after the file was published, retry export against that **identical** file to clear
the remaining frozen helper tokens. Do not retarget, automatically unfreeze or
restore a backup after the VM may have refreshed. If VM commit failed, its prior
state and input file remain; retry after resolving the actual cause. If input-file
cleanup failed after a successful VM commit, import reports failure and refuses
reimport over that account: verify protected VM state, remove only the residual
inbox file and record cleanup. No false success from partially completed cleanup.

After verified import, securely remove the helper export copy. Keep VM as the
single refresh owner; a future helper session needs fresh explicit OAuth consent,
not resurrection of transferred refresh tokens. VM signout/ChatGPT disconnect can
revoke the transferred session; current provider docs do not offer host-specific
revocation for transferred sessions. Unrelated registrations are not cleaned up.

## Official contracts

- [Registration](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [Sessions/refresh/revocation](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [VM onboarding](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms)
- [Limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
- [Usage guidance](https://developers.openai.com/siwc/ui-ux-guidelines)
- [Model reasoning](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
- [Responses parameters](https://developers.openai.com/api/reference/resources/responses/methods/create)

The offline suite uses freshly generated synthetic signing keys and mocked
provider responses, including a real **local test** HTTP callback. It never
contacts an account provider, signs into ChatGPT or verifies real subscription
inference. Keep that distinction in any downstream release notes.
