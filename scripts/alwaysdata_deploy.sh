#!/usr/bin/env bash
# Wird vom GitHub-Actions-Workflow per SSH aufgerufen (oder manuell): Code aktualisieren, Abhängigkeiten, Datenbank.
# Der Neustart der Site erfolgt getrennt über die Alwaysdata-API (Workflow-Schritt oder ~/.sea-deploy.env),
# weil der Web-Prozess auf einer anderen Maschine läuft als die SSH-Shell und von hier nicht beendet werden kann.
set -euo pipefail
cd "$(dirname "$0")/.."
git fetch --quiet origin
git reset --hard --quiet origin/main
source .venv/bin/activate
pip install -q -r requirements.txt
python -m cockpit.cli init-db
chmod +x scripts/*.sh
echo "Code-Stand: $(git log --oneline | head -1)"
if [ -f "$HOME/.sea-deploy.env" ]; then
  # shellcheck disable=SC1091
  source "$HOME/.sea-deploy.env"
  curl -fsS -X POST --basic --user "${ALWAYSDATA_API_KEY} account=${ALWAYSDATA_ACCOUNT:-y-b}:" \
    "https://api.alwaysdata.com/v1/site/${ALWAYSDATA_SITE_ID:-1092854}/restart/" && echo "Site über API neu gestartet."
else
  echo "Kein ~/.sea-deploy.env: Neustart übernimmt der Workflow (ALWAYSDATA_API_KEY) oder das Admin-Panel."
fi
