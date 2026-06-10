#!/bin/sh
# Readiness: index.html present AND every JS entry script it references exists on
# disk. The host bootstraps from Module Federation entries at the root
# (e.g. /mf-entry-bootstrap-0.js), not /assets/*.js. The Cesium and runtime
# scripts are injected by nginx sub_filter at response time (not present in the
# on-disk index.html of the read-only image), so they are skipped here.
set -e
HTML="/usr/share/nginx/html/index.html"
[ -f "$HTML" ] || { echo "index.html missing"; exit 1; }

entries=$(grep -oE '<script[^>]+src="/[^"]+\.js"' "$HTML" | grep -oE 'src="/[^"]+"' | sed 's/^src="//; s/"$//')
found=0
for e in $entries; do
    case "$e" in
        /cesium/*|/__nkz_runtime__*) continue ;;
    esac
    [ -f "/usr/share/nginx/html${e}" ] || { echo "entry script ${e} missing on disk"; exit 1; }
    found=1
done
[ "$found" = "1" ] || { echo "no on-disk entry script referenced in index.html"; exit 1; }

# Runtime config must have been written by entrypoint
[ -f "/runtime/__nkz_runtime__.js" ] || { echo "runtime config not written"; exit 1; }
echo "ready"
