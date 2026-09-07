#!/usr/bin/env bash
set -euo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-.}"
TARGET="$(cd "$TARGET" && pwd)"

if ! git -C "$TARGET" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Target is not a git repository: $TARGET" >&2
  exit 1
fi

mkdir -p \
  "$TARGET/.ai-review/agents" \
  "$TARGET/.ai-review/contracts" \
  "$TARGET/.ai-review/lib" \
  "$TARGET/.ai-review/state" \
  "$TARGET/bin"

cp "$KIT_DIR/templates/policy.md" "$TARGET/.ai-review/policy.md"
cp "$KIT_DIR/templates/model-routing.md" "$TARGET/.ai-review/model-routing.md"
cp "$KIT_DIR/templates/orchestration.yml" "$TARGET/.ai-review/orchestration.yml"
cp "$KIT_DIR/templates/context-governor.yml" "$TARGET/.ai-review/context-governor.yml"
cp "$KIT_DIR/templates/evidence-policy.yml" "$TARGET/.ai-review/evidence-policy.yml"
cp "$KIT_DIR/templates/profiles.yml" "$TARGET/.ai-review/profiles.yml"
cp "$KIT_DIR/templates/contracts/pr-contract.yml" "$TARGET/.ai-review/contracts/pr-contract.yml"
cp "$KIT_DIR/templates/lib/common.sh" "$TARGET/.ai-review/lib/common.sh"

for agent in cleanup ponytail regression security-gate pr-summarizer; do
  cp "$KIT_DIR/templates/agents/$agent.md" "$TARGET/.ai-review/agents/$agent.md"
done

if [[ ! -f "$TARGET/.ai-review/project-profile.json" ]]; then
  cp "$KIT_DIR/templates/profile/project-profile.json" "$TARGET/.ai-review/project-profile.json"
fi

for script in ai-review-plan ai-pr-ready ai-contract ai-project-profile ai-diff-budget ai-check-plan ai-log ai-metrics ai-stack-doctor; do
  cp "$KIT_DIR/bin/$script" "$TARGET/bin/$script"
  chmod +x "$TARGET/bin/$script"
done

append_managed_block() {
  local target_file="$1" block_file="$2"
  local start='<!-- ai-agent-stack:start -->' end='<!-- ai-agent-stack:end -->'
  touch "$target_file"
  if grep -Fq "$start" "$target_file"; then
    if ! grep -Fq "$end" "$target_file"; then
      echo "Refusing to update malformed managed block in: $target_file" >&2
      return 1
    fi
    python3 - "$target_file" "$block_file" <<'PY'
import pathlib, sys
p=pathlib.Path(sys.argv[1]); b=pathlib.Path(sys.argv[2]).read_text().rstrip(); s=p.read_text()
start='<!-- ai-agent-stack:start -->'; end='<!-- ai-agent-stack:end -->'
a=s.index(start); z=s.index(end,a)+len(end)
p.write_text((s[:a]+b+s[z:]).rstrip()+'\n')
PY
  else
    [[ ! -s "$target_file" ]] || printf '\n' >> "$target_file"
    cat "$block_file" >> "$target_file"; printf '\n' >> "$target_file"
  fi
}
append_managed_block "$TARGET/CLAUDE.md" "$KIT_DIR/templates/CLAUDE.block.md"
append_managed_block "$TARGET/AGENTS.md" "$KIT_DIR/templates/AGENTS.block.md"

# Keep transient orchestration state local without modifying the project's tracked .gitignore.
GIT_DIR="$(git -C "$TARGET" rev-parse --git-dir)"
[[ "$GIT_DIR" = /* ]] || GIT_DIR="$TARGET/$GIT_DIR"
mkdir -p "$GIT_DIR/info"
EXCLUDE="$GIT_DIR/info/exclude"
touch "$EXCLUDE"
for pattern in '.ai-review/current-contract.yml' '.ai-review/state/' '.ai-review/telemetry.jsonl'; do
  grep -Fxq "$pattern" "$EXCLUDE" || echo "$pattern" >> "$EXCLUDE"
done

cat <<MSG
Installed AI Agent Stack into:
  $TARGET

Core config:
  .ai-review/policy.md
  .ai-review/orchestration.yml
  .ai-review/profiles.yml
  .ai-review/context-governor.yml
  .ai-review/evidence-policy.yml
  .ai-review/model-routing.md

Agents:
  regression | security-gate | cleanup | ponytail | pr-summarizer

Commands:
  ai-contract | ai-project-profile | ai-check-plan | ai-review-plan
  ai-diff-budget | ai-pr-ready | ai-log | ai-metrics
MSG

if command -v codegraph >/dev/null 2>&1; then
  codegraph status "$TARGET" >/dev/null 2>&1 || {
    echo; echo "CodeGraph is installed but this project is not initialized. Run:"
    echo "  cd \"$TARGET\" && codegraph init"
  }
fi

echo
echo "Recommended first run:"
echo "  cd \"$TARGET\""
echo "  ./bin/ai-project-profile"
echo "  ./bin/ai-contract init"
echo "  ./bin/ai-review-plan main --profile standard"
