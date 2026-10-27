#!/usr/bin/env bash
# Wird vom GitHub-Actions-Workflow per SSH aufgerufen (oder manuell): Code aktualisieren, Abhängigkeiten, Neustart.
set -euo pipefail
cd "$(dirname "$0")/.."
git fetch --quiet origin
git reset --hard --quiet origin/main
source .venv/bin/activate
pip install -q -r requirements.txt
python -m cockpit.cli init-db
# Neustart über die Alwaysdata-API (ALWAYSDATA_API_KEY und ALWAYSDATA_SITE_ID in ~/.sea-deploy.env)
if [ -f "$HOME/.sea-deploy.env" ]; then
  # shellcheck disable=SC1091
  source "$HOME/.sea-deploy.env"
  curl -fsS -X POST --basic --user "${ALWAYSDATA_API_KEY} account=${ALWAYSDATA_ACCOUNT:-y-b}:" \
    "https://api.alwaysdata.com/v1/site/${ALWAYSDATA_SITE_ID}/restart/" && echo "Site neu gestartet."
else
  echo "Hinweis: ~/.sea-deploy.env fehlt, Site bitte im Admin-Panel neu starten."
fi
