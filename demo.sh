#!/usr/bin/env bash
# Render every scene from the sample data in demo/ to out/<scene>.png.
#
#   ./demo.sh                 the Spectra 6 panel (Inky Impression 7.3", 2025)
#   DESK_PANEL=acep7 ./demo.sh  the older 7-colour panel (2023)
#
# Everything on screen is made up: a fictional day, customer and meeting card.
# No network: the weather comes from demo/cache-template.json.
set -euo pipefail
cd "$(dirname "$0")"

render() {  # render <scene> <HH:MM>
  local cache
  cache=$(mktemp -t desk-demo-cache.XXXXXX)
  cp demo/cache-template.json "$cache"
  DESK_DATA=demo DESK_CACHE="$cache" DESK_WORK_DOMAINS=yourco.example DESK_FAMILY_ACCOUNTS=family@example.com \
    DESK_AGENT_NAME=Hunter DESK_HEADLINE_RSS="" \
    python3 desk_companion.py 800 480 --out "out/$1.png" --now "2026-10-01T$2:00+01:00" --verbose
  rm -f "$cache"
}

mkdir -p out
render morning 07:15
render work 09:30
render card 09:45
render night 22:30
