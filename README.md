# SEA-Cockpit der Copilotenschule

Eigener Dienst, der Google Ads (und später Microsoft Advertising) täglich ausliest, die Zahlen dauerhaft speichert,
Experimente vergleicht, Auffälligkeiten meldet und Verbesserungen vorschlägt. Das Cockpit ändert nichts in den
Werbekonten. Chris setzt um, meldet die Umsetzung zurück, das Cockpit misst die Wirkung.

Oberfläche: Web-App mit Login (Chris, Martin). Zusätzlich ein MCP-Endpunkt, damit ChatGPT im Team-Workspace oder
Claude dieselben Werkzeuge nutzen können. Sprachmodell im Hintergrund: OpenAI API (Reports, Vorschläge, Chat).

Dokumentation:

- `docs/KONZEPT.md` – was das Cockpit tut, Datenmodell, Regeln, Phasen
- `docs/API-ZUGANG.md` – Google Ads API und Microsoft Advertising API einrichten (der kritische Pfad)
- `docs/DEPLOY-ALWAYSDATA.md` – Betrieb auf Alwaysdata, Scheduled Tasks, GitHub Actions
- `docs/CHATGPT-MCP.md` – Einbindung im ChatGPT-Team-Workspace
- `docs/BRIEFING-CHRIS.md` – eine Seite für Chris
- `knowledge/` – Wissensbasis fürs Modell (Analysen, Entscheidungen)

## Schnellstart lokal

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # mindestens COCKPIT_SECRET_KEY, COCKPIT_USERS, MCP_TOKEN setzen
python -m cockpit.cli hash-password   # Hash für COCKPIT_USERS
python -m cockpit.cli init-db
python -m cockpit.cli seed-demo       # synthetische Daten, bis der API-Zugang steht (DEMO_MODE=1)
python -m cockpit.cli serve           # http://127.0.0.1:8000
python -m pytest -q
```

## Befehle

| Befehl | Zweck |
|---|---|
| `python -m cockpit.cli nightly` | Abzug der letzten 7 Tage (Google, Microsoft, Trello) + Alerts + Benachrichtigung |
| `python -m cockpit.cli backfill --days 120` | Historie einmalig nachladen |
| `python -m cockpit.cli weekly` | Wochenreport erzeugen und versenden (`--no-send` zum Testen) |
| `python -m cockpit.cli monthly` | Monatsreport mit Experiment-Bilanz |
| `python -m cockpit.cli test-connections` | Alle Verbindungen prüfen |
| `python -m cockpit.cli google-auth` | OAuth-Refresh-Token für Google Ads erzeugen (einmalig, lokal) |
| `python -m cockpit.cli ms-auth` | OAuth-Refresh-Token für Microsoft Advertising erzeugen |
| `python -m cockpit.cli seed-demo` / `purge-demo` | Demo-Daten rein/raus |

## Aufbau

```
cockpit/
  main.py            FastAPI: Seiten, JSON-API, Webhook, MCP-Mount
  models.py          Tageswerte je Ebene, Änderungen, Leads, Experimente, Vorschläge, Alerts, Reports
  analysis/          kpis (Rechnen), anomalies (Regeln), experiments (Vorher/Nachher)
  connectors/        google_ads (GAQL), microsoft_ads (Reporting API), trello (Leads)
  llm/               tools (Werkzeuge über die DB), client (Responses API), reports, chat, prompts
  mcp_server.py      MCP-Endpunkt mit denselben Werkzeugen
  jobs.py / cli.py   Nightly, Weekly, Monthly
  templates/, static/  Oberfläche (Jinja2, Chart.js)
knowledge/           Wissensbasis (Markdown)
docs/                Dokumentation
scripts/             Alwaysdata: Setup, Start, Deploy
```

## Sicherheit

- Zugangsdaten nur in `.env` auf dem Server, nie im Repository (siehe `.gitignore`).
- OAuth-Zugriff auf Google Ads ist technisch nicht auf Lesen beschränkbar; das Cockpit enthält bewusst keinen
  Code, der Mutationen ausführt. `run_sql` im Chat/MCP erlaubt nur SELECT.
- MCP-Endpunkt nur über geheimen Pfad bzw. Bearer-Token; OAuth als Ausbaustufe (siehe docs/CHATGPT-MCP.md).
- Bookings-Webhook verlangt `X-Webhook-Secret`.
