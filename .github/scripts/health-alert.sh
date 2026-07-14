#!/usr/bin/env bash
# File (or comment on) a health-alert issue assigned to the repo owner, so they
# get a GitHub notification when a deployment health check fails.
#
# Env: GH_TOKEN, REPO (owner/name), OWNER, RUN_URL, SCOPE (which check failed).
set -uo pipefail

gh label create health-alert --repo "$REPO" --color b60205 \
  --description "Deployment health-check failure" --force 2>/dev/null || true

body=$(printf '%s\n' \
  "A scheduled **deployment health check failed** (${SCOPE:-unknown})." \
  "" \
  "- Run: $RUN_URL" \
  "- See the run log for the failing endpoint(s)." \
  "" \
  "cc @$OWNER")

# De-dup: comment on the existing open alert instead of piling up new issues.
existing=$(gh issue list --repo "$REPO" --label health-alert --state open \
  --json number --jq '.[0].number' 2>/dev/null || echo "")

if [ -n "$existing" ]; then
  gh issue comment "$existing" --repo "$REPO" --body "$body"
else
  gh issue create --repo "$REPO" \
    --title "🚨 Deployment health check failed — $(date -u +%Y-%m-%d)" \
    --label health-alert \
    --assignee "$OWNER" \
    --body "$body"
fi
