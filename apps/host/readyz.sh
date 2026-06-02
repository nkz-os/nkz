#!/bin/sh
# Readiness: index.html present AND its referenced entry chunk exists on disk.
set -e
HTML="/usr/share/nginx/html/index.html"
[ -f "$HTML" ] || { echo "index.html missing"; exit 1; }
# Extract the first hashed JS module the index references (Vite: /assets/xxxx.js)
entry=$(grep -oE '/assets/[A-Za-z0-9_.-]+\.js' "$HTML" | head -1)
[ -n "$entry" ] || { echo "no entry asset referenced in index.html"; exit 1; }
[ -f "/usr/share/nginx/html${entry}" ] || { echo "entry asset ${entry} missing on disk"; exit 1; }
# Runtime config must have been written by entrypoint
[ -f "/runtime/__nkz_runtime__.js" ] || { echo "runtime config not written"; exit 1; }
echo "ready"
