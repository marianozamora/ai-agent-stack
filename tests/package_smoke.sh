#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

python3 -m pip wheel --no-deps --wheel-dir "$TMP/dist" "$ROOT" >/dev/null
python3 -m venv "$TMP/venv"
"$TMP/venv/bin/python" -m pip install --no-deps "$TMP"/dist/*.whl >/dev/null

INSTALLED_VERSION="$("$TMP/venv/bin/ai" --version)"
EXPECTED_VERSION="$(tr -d '\n' < "$ROOT/VERSION")"
[ "$INSTALLED_VERSION" = "$EXPECTED_VERSION" ]
"$TMP/venv/bin/ai" --help >/dev/null
mkdir "$TMP/repo"
git -C "$TMP/repo" init -q
(cd "$TMP/repo" && HOME="$TMP/home" XDG_CONFIG_HOME="$TMP/config" "$TMP/venv/bin/ai" init >/dev/null)
(cd "$TMP/repo" && HOME="$TMP/home" XDG_CONFIG_HOME="$TMP/config" "$TMP/venv/bin/ai" skill list >/dev/null)
(cd "$TMP/repo" && HOME="$TMP/home" XDG_CONFIG_HOME="$TMP/config" "$TMP/venv/bin/ai" benchmark --json >/dev/null)

printf '%s\n' 'package smoke: PASS'
