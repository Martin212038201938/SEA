# Konzept SEA-Cockpit

Stand 27.10.2026. Entscheidungen aus dem Auftragsgespräch mit Martin, Antworten auf die 15 Klärungsfragen eingearbeitet.

## 1. Auftrag

Ein Werkzeug, das die Google-Ads-Kampagnen der Copilotenschule (später auch Microsoft Advertising) regelmäßig
auswertet, die Zahlen dauerhaft speichert, Experimente vergleichbar macht und Verbesserungen vorschlägt. Keine
automatischen Änderungen in den Werbekonten. Vorschläge kurz, für Leute, die Google Ads bedienen können: Was,
warum, erwarteter Effekt, Schritte. Hauptnutzer ist Chris, unabhängig von Martins Rechner und Accounts.

## 2. Entscheidungen

| Frage | Entscheidung | Begründung |
|---|---|---|
| Oberfläche | Eigene Web-App auf Alwaysdata mit Login; zusätzlich MCP-Endpunkt für den ChatGPT-Team-Workspace | Ein Claude-Artefakt kann für Besucher ohne Claude-Account keine Datenbank, keine KI und keine Connectoren nutzen; ChatGPT allein kann nichts planmäßig ziehen und speichern |
| Backend | Python (FastAPI, SQLAlchemy), SQLite | Vorhandene Alwaysdata-Infrastruktur, versionierbar, geringe Kosten |
| Datenquelle | Google Ads API direkt (GAQL), Microsoft Advertising Reporting API | Volle Tiefe inkl. Suchbegriffe, Änderungsverlauf, Auktionsdaten |
| Ebenen täglich | Kampagne, Anzeigengruppe, Keyword, Suchbegriff, Anzeige, Asset; Änderungsverlauf 14 Tage rollierend; Auktionsdaten wöchentlich | Von Martin so gewählt |
| Experimente | Chris legt sie an (Hypothese, Start, Umfang); Cockpit zieht zusätzlich den Änderungsverlauf und markiert Störfaktoren | Kombination aus manueller Absicht und automatischer Kontrolle |
| Umsetzungs-Tracking | Chris meldet; Cockpit verifiziert gegen den Änderungsverlauf; jede Umsetzung wird zum Experiment | Wirkung messbar |
| Wahrheitsebene | Trello-Board (Kontaktformular-Leads, Status, Auftragswert, Erlös) plus Bookings-Buchungen per Power-Automate-Webhook | Ads-Conversions sind unvollständig (Bookings auf Microsoft-Domain) |
| Reports | Tägliche Alerts bei Schwellenverletzung, Wochenreport montags, Monatsreport mit Experiment-Bilanz; Versand Teams und E-Mail | Von Martin so gewählt |
| Sprachmodell | OpenAI API im Backend (`gpt-6.1-terra`, konfigurierbar) für Reports, Vorschläge, Chat | Gleicher Anbieter wie der Workspace, vorhandenes Guthaben |
| Wissensbasis | Chris' ChatGPT-Analysen plus Konto-Stammdaten im Ordner `knowledge/` | Von Martin gewählt; erweiterbar |
| Betrieb | Martin deployt (GitHub → Alwaysdata), Chris nutzt | Chris hat keinen Cloud-Account |

## 3. Architektur

```
Google Ads API ──┐                         ┌── Web-Oberfläche (Login) ── Chris, Martin
Microsoft Ads ───┼─ Nightly-Job ─► SQLite ─┼── MCP-Endpunkt ── ChatGPT-Workspace, Claude
Trello ──────────┤   (Alwaysdata Cron)     ├── Alerts ── Teams / E-Mail
Bookings-Webhook ┘                         └── Weekly/Monthly ── OpenAI API ── Report + Vorschläge
```

Alles läuft auf dem Alwaysdata-Account y-b. Keine Abhängigkeit von Martins Mac.

## 4. Datenmodell (Kurzform)

Tageswerte je Ebene (`campaign_daily`, `adgroup_daily`, `keyword_daily`, `search_term_daily`, `ad_daily`, `asset_daily`)
mit Impressionen, Klicks, Kosten (EUR), Conversions, Conversion-Wert, alle Conversions; Kampagne zusätzlich Status,
Gebotsstrategie, Budget, Enddatum, Impression Share und Verluste durch Budget/Rang; Keyword zusätzlich Matchtyp,
Qualitätsfaktor, Auslieferungsstatus; Suchbegriff mit auslösendem Keyword und Status (hinzugefügt/ausgeschlossen).
`auction_insights` je Woche und Domain. `change_events` mit Zeitpunkt, Nutzer, Ressource, Feldern, alt/neu.
`conversion_actions` (Kategorie, primär/sekundär) für Datenqualitätsprüfungen. `leads` (Quelle, Datum, Firma, Status,
Kanal, Art, Auftragswert, Erlös). `experiments`, `suggestions`, `alerts`, `reports`, `chat_messages`, `job_runs`, `settings`.

Upsert nach Schlüssel; der Nightly-Job lädt die letzten 7 Tage immer neu, weil Conversions nachreifen.

## 5. Regeln ohne KI (deterministisch)

Alerts (Schwellen im Admin änderbar): CPC-Sprung (3 Tage vs. Median 14 Tage, +40 %), Tagesausgabe über 1,3× Budget,
keine Impressionen an einem Werktag, 40+ Klicks ohne Conversion in 14 Tagen, Budget-Verlust über 25 %, Enddatum
innerhalb 14 Tagen, CPL über Abbruchschwelle (28 Tage), Suchbegriffe ab 25 € ohne Conversion (28 Tage), über 40 %
der Keywords mit geringem Suchvolumen, primäre Conversion-Aktion mit Kategorie Seitenaufruf.

Experiment-Vergleich: Basisfenster gleich lang wie Testfenster (oder fest), Kennzahlen, Wilson-Intervall der
Conversion-Rate, Störfaktoren aus dem Änderungsverlauf, Klartext-Einordnung mit Warnung bei unter 10 Conversions.

## 6. KI-Schicht

Das Modell bekommt ein Datenpaket (JSON) plus Wissensbasis und liefert nach festem Schema: Report-Markdown mit
vorgegebenen Abschnitten und 3 bis 6 Vorschläge mit Titel, Kategorie, Begründung, erwartetem Effekt, Schritten,
Priorität, Sicherheit, Aufwand, betroffenen Kampagnen, Belegen. Regeln im Systemprompt: keine erfundenen Zahlen,
Zeitraum nennen, Unsicherheit bei kleinen Stichproben, keine Nicht-Maßnahmen (PMax, AI Max, Display,
Keyword-Ausweitung ohne Negativliste), offene Vorschläge nicht wiederholen.

Chat und MCP nutzen dieselben 23 Werkzeuge über die Datenbank; `run_sql` nur SELECT. Ohne OpenAI-Schlüssel
entstehen Fallback-Reports mit Rohzahlen und Alerts, der Chat meldet die fehlende Konfiguration.

## 7. Phasen

| Phase | Inhalt | Status |
|---|---|---|
| 0 | Code, Tests, Doku, Demo-Daten | fertig (dieses Repository) |
| 1 | Deployment auf Alwaysdata, Logins, Demo-Modus für Chris | Martin, ca. 1 h |
| 2 | Google Ads API: Cloud-Projekt, Explorer-Zugang, OAuth, Backfill 120 Tage | Martin, 1–2 h plus Wartezeit |
| 3 | OpenAI-Schlüssel, Teams-Webhook, erster echter Wochenreport | Martin, 30 Min |
| 4 | Trello-Anbindung, Power-Automate-Flow für Bookings | Martin/Chris, 1–2 h |
| 5 | MCP im ChatGPT-Workspace freischalten | Admin des Workspace, 30 Min |
| 6 | Microsoft Advertising, sobald Kampagnen wieder laufen | 1 h plus Live-Test des Connectors |
| 7 | Später: OAuth für MCP, Schreibzugriff mit Freigabe (bewusst nicht in dieser Version) | offen |

## 8. Offene Punkte und Risiken

- Google-Zugriffsstufe: Explorer wird meist automatisch vergeben, Garantie gibt es nicht. Ohne Freigabe kein echter Betrieb.
- Microsoft-Connector ungetestet gegen das Live-Konto (Kampagnen pausiert).
- Attribution bleibt grob: Leads aus Trello tragen den Kanal nur, wenn jemand ihn setzt. Für die Skalierungsfrage
  reicht Kampagnen-Level-Wahrheit (Leads pro Woche gegen Kosten pro Woche).
- OpenAI-Kosten hängen von der Chat-Nutzung ab; Limit im Billing setzen.
- Conversion-Tracking in Google Ads (Test-Conversions, Seitenaufruf-Aktion als primär) ist eine Sache des Kontos,
  nicht des Cockpits; das Cockpit macht es nur sichtbar.
