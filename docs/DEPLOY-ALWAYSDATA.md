# Betrieb auf Alwaysdata (Account y-b)

Ziel: Das Cockpit läuft dauerhaft unter einer Alwaysdata-Adresse, zieht nachts Daten, verschickt montags den
Wochenreport, und Chris braucht nur Browser und Passwort.

## 1. Code auf den Server

```bash
ssh y-b@ssh-y-b.alwaysdata.net
cd ~
git clone https://github.com/Martin212038201938/SEA.git sea-cockpit     # oder: Ordner per SFTP hochladen
cd sea-cockpit
bash scripts/alwaysdata_setup.sh
```

Python-Version: Im Admin-Panel unter Environment → Python eine aktuelle Version (3.12 oder neuer) einstellen,
bevor das Setup läuft. Falls `python3` auf dem Server zu alt ist: `PYTHON=python3.12 bash scripts/alwaysdata_setup.sh`.

## 2. .env ausfüllen

`nano ~/sea-cockpit/.env` – Vorlage ist `.env.example`. Pflicht für den Start:

```
COCKPIT_SECRET_KEY=<64 Zufallszeichen, z. B. python3 -c "import secrets;print(secrets.token_urlsafe(48))">
COCKPIT_BASE_URL=https://sea.copilotenschule.de
COCKPIT_USERS=chris:<hash>;martin:<hash>      # Hash: python -m cockpit.cli hash-password
MCP_TOKEN=<48 Zufallszeichen>
DEMO_MODE=1                                    # bis der Google-Zugang steht
```

Dann `python -m cockpit.cli seed-demo`, damit Chris sofort etwas sieht.

## 3. Site anlegen

Admin-Panel → Web → Sites → „Site hinzufügen“:

| Feld | Wert |
|---|---|
| Typ | User program |
| Adressen | `sea.copilotenschule.de` (Unteradressen von alwaysdata.net erlaubt Alwaysdata nicht). DNS liegt bei IONOS: dort CNAME `sea` → `y-b.alwaysdata.net` anlegen. |
| Befehl | `/home/y-b/sea-cockpit/scripts/alwaysdata_start.sh` |
| Arbeitsverzeichnis | `sea-cockpit` (relativ zum Home-Verzeichnis; ein absoluter Pfad wird von Alwaysdata an /home/y-b/ angehängt) |
| Umgebung | nichts nötig; Alwaysdata setzt `IP` und `PORT`, das Startskript nutzt sie |
| HTTPS | Let's Encrypt aktivieren (bei alwaysdata.net-Adressen automatisch) |

Nach dem Speichern 30 Sekunden warten, dann `https://sea.copilotenschule.de/healthz` aufrufen. Antwort
`{"ok": true, …}` heißt: läuft. Fehler stehen in `~/admin/logs/sites/`.

Wenn die Site-Form eine andere Variablenbenennung erwartet, zeigt die Hilfe neben dem Feld „Befehl“ die
richtigen Namen; im Startskript dann `${IP}`/`${PORT}` anpassen.

## 4. Scheduled Tasks

Admin-Panel → Advanced → Scheduled tasks → „Task hinzufügen“, Typ „Execute the command“, Arbeitsverzeichnis
`sea-cockpit`, Zeitplan in Crontab-Syntax (`40 5 * * *`, `30 6 * * 1`, `50 6 1 * *`). Stand 27.10.2026: alle drei angelegt.

| Zeitpunkt | Befehl |
|---|---|
| täglich 05:40 | `/home/y-b/sea-cockpit/.venv/bin/python -m cockpit.cli nightly` |
| montags 06:30 | `/home/y-b/sea-cockpit/.venv/bin/python -m cockpit.cli weekly` |
| 1. des Monats 06:50 | `/home/y-b/sea-cockpit/.venv/bin/python -m cockpit.cli monthly` |

Google liefert Vortageswerte morgens vollständig; Conversions reifen nach, deshalb lädt `nightly` immer die letzten
7 Tage neu (Upsert). Zeitzone der Tasks im Panel auf Europe/Berlin stellen.

## 5. GitHub Actions (automatisches Deployment)

1. Auf dem Mac ein SSH-Schlüsselpaar nur für Deployments: `ssh-keygen -t ed25519 -f ~/.ssh/sea-deploy -N ""`.
2. Öffentlichen Schlüssel im Admin-Panel unter Remote access → SSH → Keys hinterlegen.
3. Privaten Schlüssel als Repository-Secret `ALWAYSDATA_SSH_KEY` in GitHub eintragen.
4. Optional: Auf dem Server `~/.sea-deploy.env` anlegen (sauberer Neustart über die Alwaysdata-API; ohne diese Datei beendet das Deploy-Skript den Prozess, und Alwaysdata startet ihn beim nächsten Aufruf neu):

```
ALWAYSDATA_API_KEY=<API-Key aus Profil → API>
ALWAYSDATA_ACCOUNT=y-b
ALWAYSDATA_SITE_ID=<ID der Site, steht in der URL im Admin-Panel>
```

Danach löst jeder Push auf `main` Tests und Deployment aus (`.github/workflows/deploy.yml`). Ohne Secret bleibt der
manuelle Weg: `ssh … && cd sea-cockpit && bash scripts/alwaysdata_deploy.sh`.

## 6. Datenbank

SQLite unter `data/cockpit.db` (WAL-Modus). Bei eurem Volumen (ein Konto, wenige Kampagnen) über Jahre ausreichend.
Sicherung: Scheduled Task `cp data/cockpit.db data/backup-$(date +%F).db` wöchentlich, alte Sicherungen per
`find data -name 'backup-*' -mtime +60 -delete`. Umstieg auf PostgreSQL (Alwaysdata bietet es): `DATABASE_URL`
ändern, `init-db`, Daten mit `sqlite3 .dump` / `psql` übernehmen.

## 7. Betrieb und Störungen

| Symptom | Prüfen |
|---|---|
| Seite lädt nicht | Site im Panel neu starten; `~/admin/logs/sites/` lesen; `.env` auf Tippfehler |
| Dashboard „Datenstand“ alt | Admin → Job-Läufe → letzter `nightly` → Log; häufig abgelaufener Refresh-Token |
| Kein Wochenreport | Scheduled Task gelaufen? OpenAI-Schlüssel/Guthaben? Fallback-Report ohne Modell wird trotzdem gespeichert |
| Keine Teams-Nachricht | `TEAMS_WEBHOOK_URL` prüfen, `python -m cockpit.cli test-connections` |
| Alerts zu laut/leise | Admin → Alert-Schwellen |
| Chris gesperrt | neuen Hash erzeugen, `COCKPIT_USERS` anpassen, Site neu starten |

Laufende Kosten: Alwaysdata (vorhanden), OpenAI API geschätzt im niedrigen zweistelligen Eurobereich pro Monat
bei täglichen Alerts, wöchentlichen Reports und moderater Chat-Nutzung; Limit im OpenAI-Billing setzen.
