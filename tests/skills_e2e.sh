#!/usr/bin/env bash
# End-to-end coverage of the skills engine through a real installation: routing for
# English and Spanish tasks, enable/disable round-tripping, a repo-scoped skill created
# and routed to, the stack-scoped-create-in-a-release guard (B4), survival of a repo
# skill across a reinstall, and upstream --json. See docs/spec-skills-testing.md Task 5.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
HOME_DIR="$TMP/home"
REPO="$TMP/repo"
mkdir -p "$HOME_DIR" "$REPO"
cd "$REPO"
git init -q
git config user.email e2e@example.com
git config user.name E2E
printf '%s\n' 'export const allowed = true;' > app.ts
git add . && git commit -qm init
git remote add origin git@github.com:example/ai-stack-skills-e2e.git

HOME="$HOME_DIR" XDG_CONFIG_HOME="$HOME_DIR/.config" XDG_DATA_HOME="$HOME_DIR/.local/share" "$ROOT/install.sh" >/dev/null
AI="$HOME_DIR/.local/bin/ai"
E=(HOME="$HOME_DIR" XDG_CONFIG_HOME="$HOME_DIR/.config" XDG_DATA_HOME="$HOME_DIR/.local/share")
env "${E[@]}" "$AI" init >/dev/null
env "${E[@]}" "$AI" start e2e-1 --base HEAD >/dev/null

# 2. Routing across profiles and languages.
OUT="$(env "${E[@]}" "$AI" skill list --task 'login regression returns 403' --profile standard)"
grep -q 'Recommended: diagnosing-bugs, tdd' <<<"$OUT"
OUT="$(env "${E[@]}" "$AI" skill list --task 'arregla el login que falla' --profile standard)"
grep -q 'Recommended: diagnosing-bugs, tdd' <<<"$OUT"
OUT="$(env "${E[@]}" "$AI" skill list --task 'migrar de REST a GraphQL con deprecación' --profile strict)"
grep -q 'Recommended: wayfinder, deprecation-and-migration, codebase-design' <<<"$OUT"
OUT="$(env "${E[@]}" "$AI" skill list --task 'resolve merge conflict in main' --profile standard)"
grep -q 'Recommended: resolving-merge-conflicts, tdd' <<<"$OUT"

# 3. disable/enable round-trip.
env "${E[@]}" "$AI" skill disable tdd >/dev/null
OUT="$(env "${E[@]}" "$AI" plan 'add a feature' --profile standard --base HEAD)"
! grep -q 'tdd' <<<"$OUT"
env "${E[@]}" "$AI" skill enable tdd >/dev/null
OUT="$(env "${E[@]}" "$AI" plan 'add a feature' --profile standard --base HEAD)"
grep -q 'tdd' <<<"$OUT"

# 4. Repo-scoped skill: created, routed to, listed as [repo].
env "${E[@]}" "$AI" skill create e2e-local --repo --category test --triggers zanahoria \
    --task-types feature --prompt 'E2E marker prompt.' >/dev/null
OUT="$(env "${E[@]}" "$AI" plan 'zanahoria feature' --profile standard --base HEAD)"
grep -q 'e2e-local' <<<"$OUT"
OUT="$(env "${E[@]}" "$AI" skill list)"
grep -q 'e2e-local .*\[repo\]' <<<"$OUT"

# 5. Stack-scoped create inside an installed release must be refused (B4).
if env "${E[@]}" "$AI" skill create e2e-global --category test --prompt 'p' >/tmp/e2e-b4.out 2>&1; then
  echo 'FAIL: stack-scoped `ai skill create` succeeded inside an installed release' >&2
  cat /tmp/e2e-b4.out >&2
  exit 1
fi
grep -qi 'installed release' /tmp/e2e-b4.out

# 6. Reinstall (simulates an upgrade) must not lose the repo-scoped skill.
HOME="$HOME_DIR" XDG_CONFIG_HOME="$HOME_DIR/.config" XDG_DATA_HOME="$HOME_DIR/.local/share" "$ROOT/install.sh" >/dev/null
OUT="$(env "${E[@]}" "$AI" skill list)"
grep -q 'e2e-local .*\[repo\]' <<<"$OUT"

# 7. Upstream status is valid JSON.
env "${E[@]}" "$AI" skill upstream --json | python3 -m json.tool >/dev/null

# 8. Zero-footprint: no command wrote into the checkout.
[ -z "$(git status --porcelain)" ]

printf '%s\n' 'skills-e2e: PASS'
