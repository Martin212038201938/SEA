#!/usr/bin/env bash
# Wird vom GitHub-Actions-Workflow per SSH aufgerufen (oder manuell): Code aktualisieren, Abhängigkeiten, Neustart.
set -euo pipefail
cd "$(dirname "$0")/.."
git fetch --quiet origin
git reset --hard --quiet origin/main
source .venv/bin/activate
pip install -q -r requirements.txt
python -m cockpit.cli init-db
chmod +x scripts/*.sh
# Neustart: Alwaysdata startet den „User program“-Prozess beim nächsten Aufruf neu, sobald er beendet ist.
pkill -f "uvicorn cockpit.main:app" && echo "Prozess beendet, Alwaysdata startet ihn beim nächsten Aufruf neu." || echo "Kein laufender Prozess gefunden."
# Optional sauberer Neustart über die Alwaysdata-API, wenn ~/.sea-deploy.env vorhanden ist
if [ -f "$HOME/.sea-deploy.env" ]; then
  # shellcheck disable=SC1091
  source "$HOME/.sea-deploy.env"
  curl -fsS -X POST --basic --user "${ALWAYSDATA_API_KEY} account=${ALWAYSDATA_ACCOUNT:-y-b}:" \
    "https://api.alwaysdata.com/v1/site/${ALWAYSDATA_SITE_ID}/restart/" && echo "Site über API neu gestartet."
fi
sleep 3
curl -fsS -o /dev/null -w "healthz: %{http_code}\n" -H "Host: sea.copilotenschule.de" http://127.0.0.1/healthz || true
