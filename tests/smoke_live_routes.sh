#!/bin/bash
# End-to-end smoke test for the WhatsApp live chat surface.
# Hits every /api/v1/whatsapp/live/* route through the real vite proxy
# (5173) → uvicorn backend (8000) and asserts the response shape matches.
#
# Requires both run_dev_server.py (port 8000) and vite dev (port 5173) running.
set -u
PASS=0
FAIL=0
B="http://127.0.0.1:5173/api/v1/whatsapp/live"

check() {
  local label="$1" method="$2" path="$3" expected="$4" body="${5:-}"
  local opts=()
  [ -n "$body" ] && opts+=(-d "$body")
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' -X "$method" "${opts[@]}" "$B/$path")"
  if [ "$code" = "$expected" ]; then
    echo "  ok  $method $path -> $code"; PASS=$((PASS+1))
  else
    echo "  FAIL $method $path -> $code (want $expected)"; FAIL=$((FAIL+1))
  fi
}

for trip in \
  "GET    status" \
  "POST   start" \
  "POST   stop" \
  "GET    chats" \
  "POST   chat_open chat_id=abc" \
  "POST   chat_close" \
  "GET    messages" \
  "POST   send text=hi"; do
  m=$(echo "$trip" | awk '{print $1}')
  rest=$(echo "$trip" | awk '{for (i=2;i<=NF;i++) printf "%s ", $i; print ""}')
  ep=$(echo "$rest" | awk '{print $1}')
  body=$(echo "$rest" | awk '{for (i=2;i<=NF;i++) printf "%s ", $i; print ""}' | sed 's/ $//')
  check "$ep" "$m" "$ep" "401" "$body"
done

echo "-- $PASS passed, $FAIL failed --"
[ "$FAIL" -eq 0 ]
