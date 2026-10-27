#!/usr/bin/env bash
# Startbefehl für die Alwaysdata-Site vom Typ „User program“.
# Alwaysdata übergibt die Adresse, auf der das Programm lauschen muss, in $IP und $PORT.
# Im Admin-Panel als Befehl eintragen:  /home/y-b/sea-cockpit/scripts/alwaysdata_start.sh
# Arbeitsverzeichnis:                   /home/y-b/sea-cockpit
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
exec uvicorn cockpit.main:app --host "${IP:-127.0.0.1}" --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="*"
