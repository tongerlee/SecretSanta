#!/usr/bin/env bash
# Both browser walk-throughs, each against a freshly started local site + API (in-memory database).
#   ./test/browser.sh            needs: pip install playwright pillow, and Chrome (set CHROME=/path/to/chrome if not found)
set -euo pipefail
cd "$(dirname "$0")/.."
python3 page/build.py >/dev/null
for walk in browser.py languages.py wishlist.py; do
  node test/server.mjs >/dev/null 2>&1 & server=$!
  trap 'kill $server 2>/dev/null || true' EXIT
  sleep 2
  echo "== $walk"
  python3 "test/$walk"
  kill $server; wait $server 2>/dev/null || true
done
echo "all browser walk-throughs passed; screenshots are in test/shots/"
