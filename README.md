# Homebase — personal ChatGPT-plan chat

A minimal black typed-chat browser app, with persistent threads and explicit
canonical memory. It reuses the open-source Sign in with ChatGPT (SIWC) helper
and direct public Responses API. No API-key fallback, extra site password,
uploads, external tools or dashboard. MIT applies to original code; dependencies
retain their own licenses.

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
"$HOME/.local/share/homebase-probe-env/bin/python" homebase_probe.py probe --model gpt-5.6-sol
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
token and chooses the returned GPT-6.1 Sol slug by default. To explicitly choose
GPT-5.6 Sol, run `python homebase_probe.py probe --model gpt-5.6-sol` with the
same installed virtual environment. This reuses the saved sign-in and does not
open a new browser login. It selects only the requested visible model; an
unavailable requested model never causes an automatic switch to another model.
If unavailable or ambiguous,
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


## Minimal browser chat

After validated SIWC login/import, choose the saved registration label from the
protected helper's `status`. Use the **same private data directory** for helper
and chat; both hold `session.lock` across refresh and inference. The app explicitly
selects `gpt-5.6-sol`, high reasoning, standard speed. It checks the selected
account's current visible catalog on every provider turn and rejects a missing
model or inconsistent terminal settings. Historical helper `probe` without
`--model` retains its 6.1 default; current commands and launcher pass 5.6 explicitly.

The following are placeholders, not an instruction to deploy to an unreviewed host:

```sh
python homebase_chat.py --data-dir /PRIVATE/STATE --account SAVED_LABEL --origin https://APPROVED-HOST seed --file /PRIVATE/SEED.json
python homebase_chat.py --data-dir /PRIVATE/STATE --account SAVED_LABEL --origin https://APPROVED-HOST serve --port APPROVED_LOOPBACK_PORT
```

Listener is always `127.0.0.1`. Put a reviewed HTTPS reverse proxy in front of it,
with the exact configured Host preserved. Unencrypted remote origins are refused.
For local development only, an explicit `http://127.0.0.1:PORT` origin works.
Do not expose the Python listener directly on the Internet.

In a protected operator terminal, mint a one-use, ten-minute device-pairing link:

```sh
python homebase_chat.py --data-dir /PRIVATE/STATE --account SAVED_LABEL --origin https://APPROVED-HOST pair
```

The terminal output is a **private bearer capability**. Do not put it in Git,
public/retained logs, tickets, chat, Slack or analytics. Open it directly in the
owner's phone/iPad browser over a protected delivery channel. A new link invalidates
previous pending links; successful pairing consumes it. The fragment is removed
before requests and exchanged for a seven-day HttpOnly/Secure/SameSite=Strict
cookie on HTTPS. No OAuth token enters the browser, no browser persistent storage
is used, and public visual-shell access alone cannot read data or use inference.
Pairing delegates access to this selected registration; it is not new provider
consent or a multi-user identity service. Anyone obtaining a live link can become
an owner browser. Cookie theft likewise requires session revocation.

Lost browser: run `revoke-browsers` with the same options to remove all sessions,
then mint a replacement `pair` link. Cookie loss/expiry needs another private link,
not another provider login while its grant remains valid. Server restart retains
sessions. Registration/account changes require a separate private state directory.
`Sign out` removes every owner-browser session, stops working text, and attempts
provider revocation when no worker holds the refresh lock. If a worker is active,
remote revocation is explicitly pending; after it releases the lock, run the
protected `homebase_probe.py --data-dir /PRIVATE/STATE --account SAVED_LABEL signout`
or disconnect Homebase in ChatGPT settings. Local sessions remain blocked meanwhile.
History/memory are retained, not deleted by sign-out.

The UI streams durable provider text through 700ms polling of saved messages.
Closing/reloading a tab does not terminate or lose the submitted turn. Stop marks
the reply incomplete immediately; the worker releases its provider connection at
the next stream event or the 60-second read timeout. A new send is refused until
that worker exits. Request UUIDs make transport retries idempotent. A terminal
failed reply can be retried explicitly without editing the retained draft.

## Canonical memory and context limits

Open **Memory** to inspect/edit/forget records with scope, source and revisions.
Or type one of these explicit commands (no provider inference is needed):

```text
/remember shared Title | an explicitly approved fact
/remember thread Title | a note for this thread only
/correct RECORD_ID CURRENT_REVISION | replacement fact
/forget RECORD_ID CURRENT_REVISION
```

Only explicit owner commands/forms write memory. Model/external text cannot call
an execution tool or promote inferred facts. Revisions reject stale concurrent
edits atomically. A shared fact may appear in fresh threads; a thread note and
other threads' transcripts cannot. Private seed is a version-1 JSON document with
`records` containing `id`, `title`, `text`, `source`, `pinned`; only empty memory can
be seeded. Generic example: `examples/memory.sample.json`. Keep personal seed
outside this public repository; install its reviewed private payload separately.

Each provider turn reads current records, builds a ≤2,000-character index,
always loads ≤2,500 characters of pinned role/preferences, and retrieves at most
four records/3,000 fact characters by lexical overlap with the current query.
At most 128 active records, titles ≤80 chars and facts ≤1,500 chars are supported.
The last six completed ordinary exchanges are included (each message excerpt
≤1,500 chars), plus current user text ≤8,000 chars. Older history is represented
by deterministic excerpts of up to six preceding exchanges (≤1,800 chars), not a
semantic AI summary or infinite recall. Full transcripts remain on disk.

Memory-command exchanges never enter provider history/summaries. Every response
records revision provenance for the current index and included history. Correcting
or forgetting a canonical record removes stale dependent turns from subsequent
context and summaries. This conservative rule can omit otherwise unrelated prior
turns after a memory edit; saved transcripts remain inspectable. An in-flight
provider request uses the memory snapshot from its start, and its stale output is
excluded on the next turn. Forget removes app memory, not past visible transcripts
or user text explicitly supplied again.

Durable private files: `session.json`, `session.lock`, `chat.sqlite3`, and
`chat-server.lock` under the 0700 state directory, outside source. SQLite stores
threads/messages/status/drafts, summaries, revisioned memory and hashed browser
sessions/pairing capabilities. Credentials remain solely in `session.json`.
A single process owns the chat-server lock. Atomic DB transactions survive process
restart; interrupted working messages become incomplete on startup. Use protected
consistent backups; never restore an old rotating provider token blindly.

## Tests and evidence boundaries

```sh
python -m unittest discover -s tests -v
node --check static/app.js
```

Provider tests use synthetic registrations/responses. Actual browser checks need
an independently available Playwright/Chromium test environment (not runtime deps):

```sh
PYTHONPATH=. PLAYWRIGHT_PYTHON tests/browser_check.py LOCKED_APP_PYTHON /PRIVATE/BROWSER-EVIDENCE
```

Replace `PLAYWRIGHT_PYTHON` and `LOCKED_APP_PYTHON` with their executable paths.
The browser harness launches a local server with a synthetic provider. Screenshots
from resized desktop/phone/iPad-like viewports do not prove a physical iPad,
remote HTTPS deployment, transferred account, live quota, refresh/revocation, or
real subscription-backed website inference. No real OAuth files are read by tests.


## Thread controls (HB-008)
Threads and Memory stay highlighted while their own panels are open. Both panels can be open independently. New thread creates a conversation on every press.
In Threads, Delete asks for confirmation naming the conversation. Confirming removes that conversation, draft, summary and thread-only notes; shared memories and other threads stay. A working or still-stopping reply must finish before deletion. Deleting the open conversation opens a remaining thread or a new empty conversation. Cancel leaves it intact.
Deletion removes application database records; this is not a guarantee of forensic erasure from storage or operator backups. No provider conversation is stored (store:false).

Developer regression checks: run `python -m unittest discover -s tests -p 'test_*.py' -v`. The new browser fixture uses Node plus Playwright 1.51.1 and its Chromium headless shell: `PLAYWRIGHT_MODULE=/path/to/playwright node tests/thread_controls_browser.cjs /path/to/app/python /path/to/output`. It starts an isolated loopback server with synthetic registration/provider data. CSP bypass applies only to browser-test predicate instrumentation; application CSP is unchanged.
# GitHub bridge (HB-011)

The optional private connection uses the official GitHub MCP server v2.0.0
(source `cb290407e20d9cc4a7c338179f4a30287f4e5040`) through the official Python
MCP SDK. The executable SHA-256 is
`2d563dfdafa4b9de831835051958a59c8cede22473d93aacc114e3daa715e0da`.
Dependencies are locked in `requirements.txt` for CPython 3.12 Linux x86_64;
install with `--require-hashes --only-binary=:all:`. This adds no inference API
key and preserves GPT-5.6 Sol/high/standard through the saved ChatGPT grant.

Runtime configuration lives in the private state directory at
`github/config.json` (owner-only directory 0700, files 0600). It contains
version 1, enabled boolean, owner `aboriginalien`, App ID 5223310,
installation ID, absolute `key_path`, and absolute `binary`. The executable
has owner-only 0700 permissions. The App PEM belongs only in protected runtime
storage and encrypted deployment secrets; never commit it or copy it into chat.
Setting enabled=false disables tools while preserving ordinary text chat.

The seven tools support repository/code search, file/directory reads, task
branches, one-file/multi-file commits, and unmerged pull requests. Broad App
permissions do not expose arbitrary administration, workflows, secrets,
merges, deployments, issue messaging, or raw API calls. Writes require current
owner input and fresh reads; code/multiple files use a newly created task
branch. Single-file canonical Homebase docs and AGENTS may use main. Task
branches use `homebase/<task>-<request-prefix>`.

Each turn is limited to six model rounds, twelve tool calls, 180 seconds,
20-second MCP calls, and bounded input/results. Grant locks end before GitHub
work. Completed provider output and encrypted reasoning are preserved in
private continuation only until the turn ends. Activity records verified Git
hashes/links without tool arguments or credentials. Stop prevents future
dispatch; an accepted write is checked if time remains. Unknown writes remain
blocked until fixed read-only verification proves their effects; they are
never automatically repeated. Thread deletion removes its activity records,
not remote commits. Search results are filtered by authenticated installation
inventory, but GitHub indexing/permissions may limit code search.

`push_files` has no expected-head CAS parameter. Restriction to this turn's
fresh task branch and pre/post checks mitigate races; they do not provide the
Work connector's atomic guarded-ref publication guarantee. The existing
protected deployment lane remains separate from tools exposed to the agent.


## Optional Homebase Voice V1

Audio uses a separately billed, protected OpenAI voice project. It never replaces
the subscription-backed GPT-5.6 Sol/high/default answer path. Runtime voice stays
disabled unless the protected installer enables `state/voice/config.json` (0600,
parent 0700). No credential belongs in this public repository or a browser bundle.

Pinned local VoxRT 0.1.1 assets are distributed unmodified with both original
licenses and `static/vendor/VOXRT_MANIFEST.json`. Hey Assistant threshold is 0.9
with cooldownFrames 100. Capture uses gpt-live-transcribe, manual Roger out
finalization and an exact original-thread/draft-revision/request-UUID payload.
A lost send acknowledgment requires read-only recovery and an explicit retry of
the same absent request. Typed drafts and accepted incomplete/stopped turns are
preserved. Session storage contains only a final request, expires after 24 hours,
and is cleared on acceptance, discard or signout; no audio or credentials persist.

Output uses gpt-realtime-2.1-mini to read only a server-resolved completed answer.
Audio remains buffered until returned transcript words match the intended segment.
Mismatch fails closed without another model, native speech or an automatic retry.
The microphone is released during output; Stop Speaking keeps the saved answer.
Enable voice unlocks foreground playback; background/locked-screen operation is
not promised. Physical Safari/iPad/iPhone/OpenComm2 acceptance is a separate gate.

Private usage reservations commit before requests, count uncertain attempts,
serialize audio operations and stop at the approved $20 monthly working boundary.
They are conservative accounting, not a claim of provider hard-cap enforcement.
The provider client secret can authorize multiple sessions during its short
creation window; its expiry does not itself terminate an existing session.
Paid synthetic acceptance uses the separate cumulative $5 protected test ledger.

Verification: Python unittest suite; Node voice_core/voice_recovery tests; actual
Chromium presentation/thread-controls/voice browser suites with synthetic provider
and RTC. The voice suite separately loads the real pinned WASM/model under CSP.
Protected rollout requires pinned source, code/private SQLite backups, live-job
and lease checks, regression and funding/audio proofs. Rollback restores prior
code/assets/CSP while preserving newer chat data; never overwrite live SQLite
with an old snapshot.
