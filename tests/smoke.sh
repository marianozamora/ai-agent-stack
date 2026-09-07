#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
HOME_DIR="$TMP/home"
REPO="$TMP/repo"
mkdir -p "$HOME_DIR" "$REPO"
cd "$REPO"
git init -q
git config user.email smoke@example.com
git config user.name Smoke
mkdir -p src/auth
printf '%s\n' 'export const allowed = true;' > src/auth/permissions.ts
printf '%s\n' '{"dependencies":{"next":"15.2.1"}}' > package.json
git add . && git commit -qm init
git remote add origin git@github.com:example/ai-stack-smoke.git
HOME="$HOME_DIR" XDG_CONFIG_HOME="$HOME_DIR/.config" XDG_DATA_HOME="$HOME_DIR/.local/share" "$ROOT/install.sh" >/dev/null
AI="$HOME_DIR/.local/bin/ai"
E=(HOME="$HOME_DIR" XDG_CONFIG_HOME="$HOME_DIR/.config" XDG_DATA_HOME="$HOME_DIR/.local/share")
env "${E[@]}" "$AI" init >/dev/null
OUT="$(env "${E[@]}" "$AI" skill list --task 'login regression returns 403' --profile standard)"
grep -q 'Recommended: diagnosing-bugs, tdd' <<<"$OUT"
OUT="$(env "${E[@]}" "$AI" plan 'login regression returns 403' --profile fast --base HEAD)"
grep -q 'skills:      diagnosing-bugs' <<<"$OUT"
OUT="$(env "${E[@]}" "$AI" plan 'login regression returns 403' --profile standard --base HEAD)"
grep -q 'skills:      diagnosing-bugs, tdd' <<<"$OUT"
env "${E[@]}" "$AI" handoff 'login regression' --base HEAD --evidence '403 reproduced' --next 'inspect auth' >/dev/null
env "${E[@]}" "$AI" optimize >/dev/null
[ -z "$(git status --porcelain)" ]
STATE="$(env "${E[@]}" "$AI" path)"
[ -f "$STATE/state/current-plan.json" ]
[ -f "$STATE/../../metrics.jsonl" ]
[ -f "$STATE/../../skill-overrides.json" ]
find "$STATE/handoffs" -type f | grep -q .
printf '%s\n' 'smoke: PASS'
