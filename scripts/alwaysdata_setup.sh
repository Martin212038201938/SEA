#!/usr/bin/env bash
# Einmalige Einrichtung auf dem Alwaysdata-Server (per SSH als y-b ausführen).
# Voraussetzung: Repository liegt unter /home/y-b/sea-cockpit (git clone <repo> sea-cockpit).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
$PY --version
[ -d .venv ] || $PY -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel >/dev/null
pip install -r requirements.txt
mkdir -p data
[ -f .env ] || { cp .env.example .env; echo "ACHTUNG: .env aus Vorlage angelegt – jetzt ausfüllen."; }
python -m cockpit.cli init-db
chmod +x scripts/*.sh
echo "Fertig. Nächste Schritte: .env ausfüllen, Site (User program) anlegen, Scheduled Tasks eintragen (siehe docs/DEPLOY-ALWAYSDATA.md)."
