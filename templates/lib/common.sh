#!/usr/bin/env bash

ai_die() { echo "$*" >&2; exit 1; }

ai_require_git() {
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || ai_die "Not inside a git repository."
}

ai_parse_profile_args() {
  AI_BASE="main"
  AI_PROFILE="${AI_PROFILE:-standard}"
  while (($#)); do
    case "$1" in
      --profile)
        shift; [[ $# -gt 0 ]] || ai_die "--profile requires fast|standard|strict"
        AI_PROFILE="$1"
        ;;
      --profile=*) AI_PROFILE="${1#*=}" ;;
      -h|--help) AI_SHOW_HELP=1 ;;
      *) AI_BASE="$1" ;;
    esac
    shift
  done
  case "$AI_PROFILE" in fast|standard|strict) ;; *) ai_die "Unknown profile: $AI_PROFILE" ;; esac
}

ai_profile_limits() {
  case "$AI_PROFILE" in
    fast) AI_MAX_FILES=4; AI_MAX_REVIEW_FILES=6; AI_MAX_CALLS=3; AI_MAX_ESCALATIONS=1; AI_MAX_REVIEWS=0 ;;
    standard) AI_MAX_FILES=8; AI_MAX_REVIEW_FILES=12; AI_MAX_CALLS=5; AI_MAX_ESCALATIONS=2; AI_MAX_REVIEWS=1 ;;
    strict) AI_MAX_FILES=12; AI_MAX_REVIEW_FILES=18; AI_MAX_CALLS=7; AI_MAX_ESCALATIONS=2; AI_MAX_REVIEWS=2 ;;
  esac
}

ai_collect_scope() {
  if git rev-parse --verify "$AI_BASE" >/dev/null 2>&1; then
    AI_RANGE="$AI_BASE..working-tree"
    AI_FILES="$(git diff --name-only "$AI_BASE" 2>/dev/null || true)"
    AI_NUMSTAT="$(git diff --numstat "$AI_BASE" 2>/dev/null || true)"
  else
    AI_RANGE="HEAD + working tree"
    AI_FILES="$(git diff --name-only HEAD 2>/dev/null || true)"
    AI_NUMSTAT="$(git diff --numstat HEAD 2>/dev/null || true)"
  fi

  local untracked
  untracked="$(git ls-files --others --exclude-standard || true)"
  [[ -z "$untracked" ]] || AI_FILES="${AI_FILES}${AI_FILES:+$'\n'}${untracked}"

  AI_FILES="$(printf '%s\n' "$AI_FILES" | grep -Ev '^(\.ai-review/|CLAUDE\.md$|AGENTS\.md$|bin/ai-[^/]+$)' || true)"
  AI_NUMSTAT="$(printf '%s\n' "$AI_NUMSTAT" | awk -F '\t' '$3 !~ /^(\.ai-review\/|CLAUDE\.md$|AGENTS\.md$|bin\/ai-[^\/]+$)/')"
  AI_FILE_COUNT="$(printf '%s\n' "$AI_FILES" | sed '/^$/d' | wc -l | tr -d ' ')"
  local tracked_lines untracked_lines path
  tracked_lines="$(printf '%s\n' "$AI_NUMSTAT" | awk '{if ($1 ~ /^[0-9]+$/) a+=$1; if ($2 ~ /^[0-9]+$/) d+=$2} END {print a+d+0}')"
  untracked_lines=0
  while IFS= read -r path; do
    [[ -n "$path" && -f "$path" ]] || continue
    case "$path" in .ai-review/*|CLAUDE.md|AGENTS.md|bin/ai-*) continue ;; esac
    untracked_lines=$((untracked_lines + $(wc -l < "$path" | tr -d ' ')))
  done <<< "$untracked"
  AI_CHANGED_LINES=$((tracked_lines + untracked_lines))
}

ai_classify_risk() {
  AI_RISK="LOW"
  AI_SECURITY_BOUNDARY="false"
  AI_RISK_REASON=""

  local high medium lowonly security
  high='(^|/)(auth|authentication|authorization|rbac|iam|payment|payments|billing|migration|migrations|schema|database|db|crypto|secrets?|permissions?|infra|terraform|k8s|kubernetes)(/|$)|(^|/)(Dockerfile|.*\.tf|.*\.sql)$'
  medium='(^|/)(api|services?|integrations?|workers?|queues?|jobs?|repositories?|controllers?|middleware)(/|$)'
  lowonly='(^|/)(docs?|examples?|fixtures?|snapshots?)(/|$)|\.(md|txt|snap)$'
  security='(^|/)(auth|authentication|authorization|rbac|iam|crypto|secrets?|permissions?|payments?|billing|sql|queries?|uploads?)(/|$)|(^|/).*(security|tenant|token|credential|untrusted|deserializ|sensitive).*'

  if printf '%s\n' "$AI_FILES" | grep -Eqi "$security"; then AI_SECURITY_BOUNDARY="true"; fi
  if printf '%s\n' "$AI_FILES" | grep -Eqi "$high"; then
    AI_RISK="HIGH"; AI_RISK_REASON="high-risk path/domain"
  elif printf '%s\n' "$AI_FILES" | grep -Eqi "$medium"; then
    AI_RISK="MEDIUM"; AI_RISK_REASON="business/API/integration path"
  elif [[ "$AI_CHANGED_LINES" -ge 160 || "$AI_FILE_COUNT" -ge 6 ]]; then
    if [[ -n "$AI_FILES" ]] && ! printf '%s\n' "$AI_FILES" | grep -Evqi "$lowonly"; then
      AI_RISK="MEDIUM"; AI_RISK_REASON="non-trivial behavioral diff size"
    fi
  fi

  # Profile may intentionally strengthen review, never weaken detected risk.
  if [[ "$AI_PROFILE" == "strict" && "$AI_RISK" == "LOW" ]]; then
    AI_RISK="MEDIUM"; AI_RISK_REASON="strict profile minimum"
  fi
}

ai_contract_status() {
  AI_CONTRACT=".ai-review/current-contract.yml"
  if [[ ! -f "$AI_CONTRACT" ]]; then
    AI_CONTRACT_STATUS="missing"
    return
  fi
  if grep -Eq '^objective:[[:space:]]*""[[:space:]]*$|^[[:space:]]*-[[:space:]]*""[[:space:]]*$|^risk:[[:space:]]*unknown[[:space:]]*$' "$AI_CONTRACT"; then
    AI_CONTRACT_STATUS="incomplete"
  else
    AI_CONTRACT_STATUS="present"
  fi
}

ai_codegraph_status() {
  if ! command -v codegraph >/dev/null 2>&1; then echo "unavailable"; return; fi
  if codegraph status . >/dev/null 2>&1; then echo "ready"; else echo "installed, project not initialized"; fi
}
