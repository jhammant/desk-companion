#!/usr/bin/env bash
# Try the Orbital integration end to end with the sample data, then clean up.
#
#   ./orbital/demo/try.sh
#
# Starts the mock CRM and notes (mock_server.py), a throwaway Postgres and an
# Orbital on 127.0.0.1:19022 loading orbital/vocabulary and orbital/demo, runs
# prep_card.taxiql for one attendee, then orbital_cards.py --dry-run over the
# sample day. Everything it starts is removed on exit. Needs Docker.
set -euo pipefail
cd "$(dirname "$0")/../.."

IMAGE=${ORBITAL_IMAGE:-orbitalhq/orbital:next}
NET=desk-try PG=desk-try-pg ORB=desk-try-orbital PORT=19022

cleanup() {
  docker rm -f "$ORB" "$PG" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  [[ -n "${MOCK_PID:-}" ]] && kill "$MOCK_PID" 2>/dev/null || true
}
trap cleanup EXIT

MOCK_HOST=0.0.0.0 python3 orbital/demo/mock_server.py >/dev/null 2>&1 &
MOCK_PID=$!
docker network create "$NET" >/dev/null
docker run -d --name "$PG" --network "$NET" -e POSTGRES_USER=orbital -e POSTGRES_PASSWORD=changeme \
  -e POSTGRES_DB=orbital postgres:15 >/dev/null
docker run -d --name "$ORB" --network "$NET" -p "127.0.0.1:$PORT:9022" --add-host host.docker.internal:host-gateway \
  -v "$PWD/orbital:/opt/service/workspace:ro" -e JAVA_OPTS="-XX:MaxRAMPercentage=50.0" \
  -e OPTIONS="--server.port=9022 --vyne.app.data.path=/tmp/orbital_data --vyne.db.username=orbital --vyne.db.password=changeme --vyne.db.host=$PG --vyne.analytics.persistResults=false --vyne.workspace.config-file=/opt/service/workspace/workspace.conf" \
  "$IMAGE" >/dev/null

echo "Waiting for Orbital on :$PORT to load both packages (about a minute) ..."
for _ in $(seq 60); do
  pkgs=$(curl -sf -m 3 "http://127.0.0.1:$PORT/api/packages" || true)
  [[ "$pkgs" == *'"vocabulary"'* && "$pkgs" == *'"demo"'* ]] && break
  sleep 5
done
sleep 5  # the schema is served a moment after the packages are listed

echo; echo "== prep_card.taxiql for priya.shah@harbourline-freight.com"
sed 's/{{email}}/priya.shah@harbourline-freight.com/' orbital/prep_card.taxiql |
  curl -s -m 60 "http://127.0.0.1:$PORT/api/taxiql" -H 'Content-Type: application/taxiql' \
    -H 'Accept: application/json' --data-binary @- |
  python3 -c "import json, sys; t = sys.stdin.read(); print(json.dumps(json.loads(t), indent=1) if t.strip() else '(no answer)')" ||
  echo "(Orbital's answer was not JSON)"

echo; echo "== orbital_cards.py --dry-run over the sample day"
DESK_DATA=demo DESK_WORK_DOMAINS=yourco.example DESK_ORBITAL_URL="http://127.0.0.1:$PORT" \
  python3 orbital_cards.py --date 2026-10-01 --dry-run
