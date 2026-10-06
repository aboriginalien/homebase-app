#!/bin/bash
# Run on the local helper. --check never starts sign-in or inference.
set -euo pipefail
cd -- "$(dirname -- "$0")"
probe_python="${HOMEBASE_PYTHON:-python3.12}"
if ! command -v "$probe_python" >/dev/null 2>&1; then
  echo 'Install official Python 3.12 for this computer, then reopen this file.'
  echo 'https://www.python.org/downloads/release/python-31210/'
  exit 1
fi
"$probe_python" -c 'import sys; assert sys.version_info[:2] == (3, 12), "Python 3.12 is required"'
if [ -n "${HOMEBASE_DATA_DIR:-}" ]; then
  probe_state="$HOMEBASE_DATA_DIR"
elif [ "$(uname -s)" = Darwin ]; then
  probe_state="$HOME/Library/Application Support/HomebaseProbe"
else
  probe_state="$HOME/.local/share/homebase-probe"
fi
"$probe_python" -c 'import pathlib,sys; assert not pathlib.Path(sys.argv[1]).expanduser().resolve().is_relative_to(pathlib.Path.cwd()), "Storage must be outside source"' "$probe_state"
# Credential storage and dependencies are outside this source checkout.
umask 077
mkdir -p "$probe_state"
probe_venv="$probe_state/venv"
if [ ! -x "$probe_venv/bin/python" ]; then
  "$probe_python" -m venv "$probe_venv"
fi
"$probe_venv/bin/python" -m pip install --disable-pip-version-check --require-hashes --only-binary=:all: -r requirements.txt
"$probe_venv/bin/python" -m unittest discover -s tests -v
if [ "${1:-}" = --check ]; then
  "$probe_venv/bin/python" homebase_probe.py --data-dir "$probe_state" status
  exit 0
fi
if [ -n "${1:-}" ]; then
  echo 'Only --check is supported by this launcher.'
  exit 1
fi
echo 'Continue with ChatGPT on this computer. One test response uses your plan allowance.'
echo 'No API-key billing fallback. No credential files should be shared.'
read -r -p 'Open the official sign-in in this computer browser? [y/N] ' probe_consent
if [ "$probe_consent" != y ] && [ "$probe_consent" != Y ]; then
  exit 0
fi
"$probe_venv/bin/python" homebase_probe.py --data-dir "$probe_state" login
"$probe_venv/bin/python" homebase_probe.py --data-dir "$probe_state" probe --model gpt-5.6-sol
