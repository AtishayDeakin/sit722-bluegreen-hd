#!/usr/bin/env bash
# Live view for the demo video: polls production twice a second and prints
# which colour answered, the version, and the HTTP status. Any request that
# fails to connect is counted, which makes "zero downtime" visible.
#   ./scripts/demo/watch-release.sh [url]
URL="${1:-http://localhost:8080}"
ok=0; failed=0; last=""
printf "Watching %s/release  (Ctrl+C to stop)\n\n" "$URL"
trap 'printf "\n%d ok, %d failed connections\n" "$ok" "$failed"; exit 0' INT
while true; do
  body=$(curl -s -m 2 -w ' %{http_code}' "$URL/release")
  code="${body##* }"
  json="${body% *}"
  now=$(date +%H:%M:%S)
  if [ "$code" = "200" ]; then
    ok=$((ok + 1))
    colour=$(echo "$json" | sed -n 's/.*"color":"\([a-z]*\)".*/\1/p')
    version=$(echo "$json" | sed -n 's/.*"version":"\([0-9a-f]\{7\}\).*/\1/p')
    case "$colour" in blue) c="\033[44;97m";; green) c="\033[42;97m";; *) c="";; esac
    marker=""; [ -n "$last" ] && [ "$colour" != "$last" ] && marker="   <== traffic switched $last -> $colour"
    printf "%s  ${c} %-5s \033[0m  %s  HTTP %s   ok=%d failed=%d%s\n" "$now" "$colour" "$version" "$code" "$ok" "$failed" "$marker"
    last="$colour"
  else
    failed=$((failed + 1))
    printf "%s  \033[41;97m DOWN  \033[0m  HTTP %s   ok=%d failed=%d\n" "$now" "${code:-000}" "$ok" "$failed"
  fi
  sleep 0.5
done
