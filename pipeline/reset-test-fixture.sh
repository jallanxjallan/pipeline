#!/usr/bin/env bash
set -euo pipefail
src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python=/opt/autoscribe/.venv/bin/python
repo="$HOME/Repos/Dispatch-Test.git"
data="$HOME/Data"
stamp="$(date +%Y%m%d-%H%M%S)"
backup="$HOME/.local/state/autoscribe/reset-backups/$stamp"
mkdir -p "$backup" "$HOME/Repos" "$data"

systemctl --user stop autoscribe-worker.service 2>/dev/null || true
systemctl --user stop autoscribe-executor.service 2>/dev/null || true
systemctl --user stop autoscribe-dispatch.service 2>/dev/null || true

for f in "$data/control.sql" "$data/ledger.sql" "$data/ledger.sql-wal" "$data/ledger.sql-shm"; do
  [[ -e "$f" ]] && cp -a "$f" "$backup/"
done
if [[ -d "$repo" ]]; then mv "$repo" "$backup/Dispatch-Test.git"; fi

rm -f "$data/ledger.sql" "$data/ledger.sql-wal" "$data/ledger.sql-shm"
"$python" "$src/fixtures/create-control-db.py" "$data/control.sql" >/dev/null
redis-cli -h 127.0.0.1 -p 6379 FLUSHDB >/dev/null

git init --bare -q "$repo"
git -C "$repo" symbolic-ref HEAD refs/heads/master
/opt/autoscribe/app/daemon/install-post-receive.sh "$repo"

AUTOSCRIBE_LEDGER_DB="$data/ledger.sql" "$python" /opt/autoscribe/app/daemon/init-ledger.py >/dev/null
systemctl --user start autoscribe-dispatch.service
systemctl --user start autoscribe-executor.service
systemctl --user start autoscribe-worker.service

printf 'Reset complete. Backup: %s\n' "$backup"
printf 'Server repo: %s (bare; dispatch branch refs/heads/master)\n' "$repo"
printf 'Control plans:\n'
"$HOME/.local/bin/asc" control plans
