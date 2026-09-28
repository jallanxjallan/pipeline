#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 1 ]] || { echo "usage: $0 /path/to/bare-repo.git" >&2; exit 2; }
repo="$(readlink -f "$1")"
[[ -d "$repo/hooks" && -f "$repo/HEAD" ]] || { echo "not a bare Git repository: $repo" >&2; exit 1; }
[[ "$(git -C "$repo" rev-parse --is-bare-repository)" == true ]] || { echo "repository is not bare: $repo" >&2; exit 1; }
hook="$repo/hooks/post-receive"
if [[ -e "$hook" ]]; then backup="${hook}.bak.$(date +%Y%m%d-%H%M%S)"; cp -a "$hook" "$backup"; echo "backed up existing hook to $backup"; fi
cat > "$hook" <<EOF
#!/usr/bin/env bash
set -u
REPO="$repo"
POKE="/opt/autoscribe/app/daemon/poke_dispatch.py"
ZERO=0000000000000000000000000000000000000000
while read -r old new ref; do
  [[ "\$ref" == refs/heads/master ]] || continue
  [[ "\$new" != "\$ZERO" ]] || continue
  if [[ "\$old" == "\$ZERO" ]]; then
    commits="\$(git -C "\$REPO" rev-list --reverse "\$new")"
  else
    commits="\$(git -C "\$REPO" rev-list --reverse "\$old..\$new")"
  fi
  while IFS= read -r commit; do
    [[ -n "\$commit" ]] || continue
    "\$POKE" --repo "\$REPO" --commit "\$commit" --ref refs/heads/master || true
  done <<< "\$commits"
done
exit 0
EOF
chmod 755 "$hook"
echo "installed $hook"
