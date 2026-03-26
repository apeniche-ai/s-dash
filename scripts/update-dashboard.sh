#!/bin/bash
#
# update-dashboard.sh — Update the Soren dashboard with agent run data
#
# Usage:
#   ./scripts/update-dashboard.sh start <agent-key> "<summary>"
#   ./scripts/update-dashboard.sh finish <agent-key> <status> <duration_seconds> "<summary>" "<details>"
#   ./scripts/update-dashboard.sh sync
#
# Examples:
#   ./scripts/update-dashboard.sh start pr-reviewer "Checking open PRs on target-test-prep/ttp"
#   ./scripts/update-dashboard.sh finish pr-reviewer success 102.5 "Reviewed 2 PRs" "Found PR #4371..."
#   ./scripts/update-dashboard.sh finish pr-reviewer error 76.5 "Failed — claude exited with code 1" "..." "claude exited with code 1"
#   ./scripts/update-dashboard.sh sync   # Re-sync agents from Soren job list
#
# Requires: GITHUB_TOKEN env var with repo write access
# Repo: apeniche-ai/soren-dashboard

set -euo pipefail

REPO="apeniche-ai/soren-dashboard"
FILE_PATH="data/runs.json"
API="https://api.github.com/repos/${REPO}/contents/${FILE_PATH}"
MAX_RUNS=500

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "Error: GITHUB_TOKEN not set" >&2
  exit 1
fi

# Fetch current file from GitHub
fetch_file() {
  curl -sf -H "Authorization: token ${GITHUB_TOKEN}" \
    -H "Accept: application/vnd.github.v3+json" \
    "${API}"
}

# Update file on GitHub
update_file() {
  local content_b64="$1"
  local sha="$2"
  local message="$3"

  curl -sf -X PUT \
    -H "Authorization: token ${GITHUB_TOKEN}" \
    -H "Accept: application/vnd.github.v3+json" \
    "${API}" \
    -d "{
      \"message\": \"${message}\",
      \"content\": \"${content_b64}\",
      \"sha\": \"${sha}\",
      \"committer\": {
        \"name\": \"Soren Bot\",
        \"email\": \"soren@noreply.github.com\"
      }
    }" > /dev/null
}

ACTION="${1:-}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

case "$ACTION" in
  start)
    AGENT_KEY="${2:?Agent key required}"
    SUMMARY="${3:-Starting up...}"

    # Fetch current data
    RESPONSE=$(fetch_file)
    SHA=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin)['sha'])")
    CONTENT=$(echo "$RESPONSE" | python3 -c "
import json, sys, base64
r = json.load(sys.stdin)
print(base64.b64decode(r['content']).decode())
")

    # Add running entry
    UPDATED=$(echo "$CONTENT" | python3 -c "
import json, sys
data = json.load(sys.stdin)
agent = '${AGENT_KEY}'
ts = '${TIMESTAMP}'
summary = '''${SUMMARY}'''

# Remove any existing 'running' entry for this agent
data['runs'] = [r for r in data['runs'] if not (r['agent'] == agent and r['status'] == 'running')]

# Add new running entry at the top
data['runs'].insert(0, {
    'agent': agent,
    'timestamp': ts,
    'duration_seconds': None,
    'status': 'running',
    'summary': summary,
    'details': None
})

# Cap runs
data['runs'] = data['runs'][:${MAX_RUNS}]
print(json.dumps(data, indent=2))
")

    # Push update
    NEW_B64=$(echo "$UPDATED" | base64)
    update_file "$NEW_B64" "$SHA" "agent: ${AGENT_KEY} started"
    echo "Dashboard updated: ${AGENT_KEY} → running"
    ;;

  finish)
    AGENT_KEY="${2:?Agent key required}"
    STATUS="${3:?Status required (success|error)}"
    DURATION="${4:?Duration required}"
    SUMMARY="${5:-}"
    DETAILS="${6:-}"
    ERRORS="${7:-}"

    # Fetch current data
    RESPONSE=$(fetch_file)
    SHA=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin)['sha'])")
    CONTENT=$(echo "$RESPONSE" | python3 -c "
import json, sys, base64
r = json.load(sys.stdin)
print(base64.b64decode(r['content']).decode())
")

    # Update running entry or add new finished entry
    UPDATED=$(echo "$CONTENT" | python3 -c "
import json, sys
data = json.load(sys.stdin)
agent = '${AGENT_KEY}'
status = '${STATUS}'
duration = ${DURATION}
summary = '''${SUMMARY}'''
details = '''${DETAILS}'''
errors_str = '''${ERRORS}'''

# Find and update running entry, or add new one
found = False
for r in data['runs']:
    if r['agent'] == agent and r['status'] == 'running':
        r['status'] = status
        r['duration_seconds'] = duration
        r['summary'] = summary
        r['details'] = details if details else None
        if errors_str:
            r['errors'] = [e.strip() for e in errors_str.split('|') if e.strip()]
        found = True
        break

if not found:
    ts = '${TIMESTAMP}'
    entry = {
        'agent': agent,
        'timestamp': ts,
        'duration_seconds': duration,
        'status': status,
        'summary': summary,
        'details': details if details else None
    }
    if errors_str:
        entry['errors'] = [e.strip() for e in errors_str.split('|') if e.strip()]
    data['runs'].insert(0, entry)

data['runs'] = data['runs'][:${MAX_RUNS}]
print(json.dumps(data, indent=2))
")

    # Push update
    NEW_B64=$(echo "$UPDATED" | base64)
    update_file "$NEW_B64" "$SHA" "agent: ${AGENT_KEY} ${STATUS} (${DURATION}s)"
    echo "Dashboard updated: ${AGENT_KEY} → ${STATUS}"
    ;;

  sync)
    echo "Sync not yet implemented — use Soren CLI to populate agents"
    ;;

  *)
    echo "Usage: $0 {start|finish|sync} [args...]" >&2
    exit 1
    ;;
esac
