#!/usr/bin/env bash
set -euo pipefail
server="$HOME/Repos/Dispatch-Test.git"
client="/tmp/autoscribe-dispatch-smoke-client"
rm -rf "$client"
git clone -q "$server" "$client"
cd "$client"
git switch -q -c master 2>/dev/null || git switch -q master
git config user.name "AutoScribe Test"
git config user.email "autoscribe-test@example.invalid"

cat > README.md <<'EOF'
AutoScribe dispatch smoke repository.
This Markdown file intentionally has no frontmatter and is repository metadata.
EOF
cat > 'Smoke Test.md' <<'EOF'
---
slug: psg.server-smoke-test
---
HHP advised on an Indonesian commercial matter. This deliberately thin passage exists only to verify the server ingest and executor preparation path.
EOF
git add README.md 'Smoke Test.md'
git commit -q -m 'Initial non-dispatch fixture'
git push -q origin master

plan_line="$(asc control plans | grep -F $'HHP Recast Case Draft for Client Review\t' | head -1)"
[[ -n "$plan_line" ]] || { echo "fixture plan not found" >&2; exit 1; }
printf '\nThis sentence was committed by the clean 0.6.1 dispatch smoke test.\n' >> 'Smoke Test.md'
git add 'Smoke Test.md'
git commit -q -m 'AutoScribe server smoke dispatch' -m "Plan: $plan_line"
git push -q origin master
printf 'Dispatch commit: '; git rev-parse HEAD
printf 'Copied plan line: %s\n' "$plan_line"
