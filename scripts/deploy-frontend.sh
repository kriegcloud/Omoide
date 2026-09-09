#!/usr/bin/env bash
# Build the SPA and swap it into the running workstation container's /app/static bind
# mount. No container restart, no in-process task is killed.
#
# Usage: scripts/deploy-frontend.sh [checkout-dir]
#   checkout-dir  git checkout or worktree to build (default: the repo this script lives in)
#
# Reads HOST_DATA_DIR and PORT from <repo>/.env (the compose project file) unless they are
# already exported. The container mounts "$HOST_DATA_DIR/static" read-only at /app/static
# (see docker-compose.workstation.yml), so a build plus an rsync is the whole deploy.
# rsync copies assets/ before index.html and only deletes stale files after the transfer,
# so a request served mid-copy still resolves either the old or the new bundle, never a mix
# with missing chunks.
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src_dir="${1:-$repo_dir}"
src_dir="$(cd "$src_dir" && pwd)"

if [[ -z "${HOST_DATA_DIR:-}" || -z "${PORT:-}" ]]; then
  if [[ -f "$repo_dir/.env" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$repo_dir/.env"; set +a
  fi
fi
: "${HOST_DATA_DIR:?HOST_DATA_DIR must be set (export it or put it in $repo_dir/.env)}"
port="${PORT:-8123}"
static_dir="$HOST_DATA_DIR/static"

echo "==> building frontend in $src_dir/frontend"
(cd "$src_dir/frontend" && npm run build --silent)
[[ -f "$src_dir/frontend/dist/index.html" ]] || { echo "build produced no dist/index.html" >&2; exit 1; }

mkdir -p "$static_dir"
echo "==> syncing dist/ -> $static_dir"
rsync -a --delete-after "$src_dir/frontend/dist/" "$static_dir/"

want="$(grep -o 'assets/index-[A-Za-z0-9_-]*\.js' "$src_dir/frontend/dist/index.html" | head -1)"
got="$(curl -fsS "http://127.0.0.1:${port}/" | grep -o 'assets/index-[A-Za-z0-9_-]*\.js' | head -1 || true)"
if [[ -n "$want" && "$want" == "$got" ]]; then
  echo "==> live: http://127.0.0.1:${port}/ serves $want"
else
  echo "==> synced, but the live page serves '${got:-nothing}' (expected $want)." >&2
  echo "    Is the static bind mount active? Recreate once with docker compose up -d." >&2
  exit 2
fi
