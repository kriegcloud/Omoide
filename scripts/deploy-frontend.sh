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
# Publish assets before index.html. Retain previous chunks for open browser tabs;
# ASSET_RETENTION_DAYS (default 7) controls pruning of expired, unused assets.
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

retention_days="${ASSET_RETENTION_DAYS:-7}"
[[ "$retention_days" =~ ^[0-9]+$ ]] || { echo "ASSET_RETENTION_DAYS must be a non-negative integer" >&2; exit 1; }
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

# Parse actual HTML attributes, including CSS, module preloads and root icons.
# External/data URLs are not served by this workstation deployment.
python3 - "$src_dir/frontend/dist/index.html" > "$work_dir/assets" <<'PYTHON'
import sys
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urlsplit

class References(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = set()

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key not in {"src", "href"} or not value:
                continue
            parsed = urlsplit(value)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            path = "/" + unquote(parsed.path).lstrip("/")
            url = quote(path, safe="/")
            if parsed.query:
                url += "?" + parsed.query
            self.urls.add(url)

parser = References()
with open(sys.argv[1], encoding="utf-8") as source:
    parser.feed(source.read())
for url in sorted(parser.urls):
    print(url)
PYTHON

mkdir -p "$static_dir"
# Retention begins when a generation is retired, not at its original build time.
# The first run has no generation manifest: grant every preexisting chunk one grace period.
python3 - "$static_dir" <<'PYTHON'
import json
import sys
from pathlib import Path

static = Path(sys.argv[1]).resolve()
manifest = static / ".omoide-current-assets.json"
if manifest.exists():
    outgoing = [static / relative for relative in json.loads(manifest.read_text(encoding="utf-8"))]
else:
    outgoing = list((static / "assets").rglob("*"))
for asset in outgoing:
    if asset.is_file() and not asset.is_symlink() and asset.resolve().is_relative_to(static / "assets"):
        asset.touch()
PYTHON

echo "==> syncing dist/ -> $static_dir"
rsync -a --exclude=index.html "$src_dir/frontend/dist/" "$static_dir/"
rsync -a "$src_dir/frontend/dist/index.html" "$static_dir/index.html"

curl -fsS "http://127.0.0.1:${port}/" -o "$work_dir/live-index.html"
if ! python3 - "$src_dir/frontend/dist/index.html" "$work_dir/live-index.html" <<'PYTHON'
import json
import re
import sys
from pathlib import Path

expected, live = (Path(path).read_text(encoding="utf-8") for path in sys.argv[1:3])
if live != expected:
    # spa_catch_all injects exactly this JSON assignment immediately before </head>.
    match = re.search(r"<script>window\.runtimeConfig = (.*?);</script>(?=</head>)", live, re.DOTALL)
    if match:
        try:
            valid_config = isinstance(json.loads(match[1]), dict)
        except ValueError:
            valid_config = False
        if valid_config:
            live = live[:match.start()] + live[match.end():]
sys.exit(0 if live == expected else 1)
PYTHON
then
  echo "==> synced, but the live page does not match the new index.html." >&2
  echo "    Is the static bind mount active? Recreate once with docker compose up -d." >&2
  exit 2
fi
while IFS= read -r asset; do
  status="$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:${port}${asset}")"
  if [[ "$status" != 200 ]]; then
    echo "==> asset verification failed: $asset returned HTTP $status" >&2
    exit 3
  fi
done < "$work_dir/assets"

# Protect every asset in the current build, including lazy chunks that index.html
# does not reference directly. Old chunks remain available for the retention window.
python3 - "$static_dir" "$src_dir/frontend/dist" "$work_dir/assets" "$retention_days" <<'PYTHON'
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

static, build, references = map(Path, sys.argv[1:4])
current_assets = [
    str(asset.relative_to(build))
    for asset in (build / "assets").rglob("*") if asset.is_file()
]
for relative in current_assets:
    (static / relative).touch()
with tempfile.NamedTemporaryFile(mode="w", dir=static, prefix=".omoide-assets-", delete=False) as handle:
    json.dump(sorted(current_assets), handle)
    manifest_temp = Path(handle.name)
try:
    os.replace(manifest_temp, static / ".omoide-current-assets.json")
finally:
    manifest_temp.unlink(missing_ok=True)
cutoff = time.time() - int(sys.argv[4]) * 86400
protected = {
    unquote(urlsplit(url).path).lstrip("/")
    for url in references.read_text(encoding="utf-8").splitlines()
}
for asset in (static / "assets").rglob("*"):
    if not asset.is_file() or asset.is_symlink():
        continue
    relative = asset.relative_to(static)
    if str(relative) in protected or (build / relative).exists():
        continue
    if asset.stat().st_mtime < cutoff:
        asset.unlink()
PYTHON

echo "==> live: http://127.0.0.1:${port}/ serves the new index and every referenced asset"
