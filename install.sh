#!/usr/bin/env bash
set -euo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-.}"
TARGET="$(cd "$TARGET" && pwd)"

if ! git -C "$TARGET" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Target is not a git repository: $TARGET" >&2
  exit 1
fi

mkdir -p "$TARGET/.ai-review/agents" "$TARGET/bin"
cp "$KIT_DIR/templates/policy.md" "$TARGET/.ai-review/policy.md"
cp "$KIT_DIR/templates/model-routing.md" "$TARGET/.ai-review/model-routing.md"
cp "$KIT_DIR/templates/orchestration.yml" "$TARGET/.ai-review/orchestration.yml"
cp "$KIT_DIR/templates/agents/cleanup.md" "$TARGET/.ai-review/agents/cleanup.md"
cp "$KIT_DIR/templates/agents/ponytail.md" "$TARGET/.ai-review/agents/ponytail.md"
cp "$KIT_DIR/bin/ai-review-plan" "$TARGET/bin/ai-review-plan"
cp "$KIT_DIR/bin/ai-pr-ready" "$TARGET/bin/ai-pr-ready"
cp "$KIT_DIR/bin/ai-stack-doctor" "$TARGET/bin/ai-stack-doctor"
chmod +x "$TARGET/bin/ai-review-plan" "$TARGET/bin/ai-pr-ready" "$TARGET/bin/ai-stack-doctor"

append_managed_block() {
  local target_file="$1"
  local block_file="$2"
  local start='<!-- ai-agent-stack:start -->'
  local end='<!-- ai-agent-stack:end -->'

  touch "$target_file"

  if grep -Fq "$start" "$target_file"; then
    if ! grep -Fq "$end" "$target_file"; then
      echo "Refusing to update malformed managed block in: $target_file" >&2
      return 1
    fi
    python3 - "$target_file" "$block_file" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
b = pathlib.Path(sys.argv[2]).read_text().rstrip()
s = p.read_text()
start = '<!-- ai-agent-stack:start -->'
end = '<!-- ai-agent-stack:end -->'
a = s.index(start)
z = s.index(end, a) + len(end)
p.write_text((s[:a] + b + s[z:]).rstrip() + '\n')
PY
  else
    if [[ -s "$target_file" ]]; then printf '\n' >> "$target_file"; fi
    cat "$block_file" >> "$target_file"
    printf '\n' >> "$target_file"
  fi
}

append_managed_block "$TARGET/CLAUDE.md" "$KIT_DIR/templates/CLAUDE.block.md"
append_managed_block "$TARGET/AGENTS.md" "$KIT_DIR/templates/AGENTS.block.md"

cat <<MSG
Installed AI Agent Stack into:
  $TARGET

Added/updated:
  .ai-review/policy.md
  .ai-review/model-routing.md
  .ai-review/orchestration.yml
  .ai-review/agents/cleanup.md
  .ai-review/agents/ponytail.md
  bin/ai-review-plan
  bin/ai-pr-ready
  bin/ai-stack-doctor
  CLAUDE.md managed block
  AGENTS.md managed block
MSG

if command -v codegraph >/dev/null 2>&1; then
  if ! codegraph status "$TARGET" >/dev/null 2>&1; then
    echo
    echo "CodeGraph is installed but this project is not initialized. Run:"
    echo "  cd \"$TARGET\" && codegraph init"
  fi
fi

echo
echo "Next:"
echo "  cd \"$TARGET\""
echo "  ./bin/ai-review-plan main"
echo "  ./bin/ai-pr-ready main"
