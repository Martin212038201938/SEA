# API-Zugang einrichten: Google Ads und Microsoft Advertising

Das ist der Teil, an dem das Projekt hängt. Code und Server sind fertig; ohne diese Zugänge gibt es nur Demo-Daten.
Zeitbedarf Google: 1 bis 2 Stunden eigene Arbeit plus Wartezeit auf die Freigabe (oft automatisiert, kann Tage dauern).
Microsoft: ca. 1 Stunde.

Quellen (Stand 27.10.2026): Google-Dokumentation zu Zugriffsstufen (Developer-Tokens am 09.09.2026 abgeschafft,
Zugriffsstufe hängt am Cloud-Projekt), Microsoft-Dokumentation „Get started“ zur Advertising API.

## Teil A: Google Ads API (Konto 748-342-2182, Login yellowboatschulung@gmail.com)

**Stand 27.10.2026, umgesetzt:** Das Cloud-Projekt `copilotenschule-dashboard` (ID bubbly-observer-500418-b8) hat die
Google Ads API aktiviert und bereits die Zugriffsstufe **Explorer**. Statt OAuth-Refresh-Token nutzt das Cockpit das
**Dienstkonto** `sea-reporting-reader@bubbly-observer-500418-b8.iam.gserviceaccount.com`; der JSON-Schlüssel liegt auf
dem Server unter `data/google-service-account.json` (`GOOGLE_ADS_SERVICE_ACCOUNT_FILE`). Das Dienstkonto muss im
Google-Ads-Konto als Nutzer eingetragen sein (Verwaltung → Zugriff und Sicherheit → +, Zugriffsebene „Nur Lesen“ reicht;
laut Google-Doku ohne Einladungsbestätigung). Die Schritte A1–A5 unten beschreiben den OAuth-Weg als Alternative.

### A1. Google-Cloud-Projekt anlegen

1. https://console.cloud.google.com mit yellowboatschulung@gmail.com anmelden.
2. Oben „Projekt auswählen“ → „Neues Projekt“. Name: `copilotenschule-sea-cockpit`. Ein eigenes Projekt nur für diese
   Integration, das verlangen Googles Developer Policies.
3. Links „APIs und Dienste“ → „Bibliothek“ → „Google Ads API“ suchen → „Aktivieren“.

### A2. Zugriffsstufe beantragen

1. In der Cloud Console die Seite „Google Ads API“ öffnen (APIs und Dienste → aktivierte APIs → Google Ads API → Übersicht).
2. Dort steht die Zugriffsstufe des Projekts. Neu angelegte Projekte haben „Test“ (nur Testkonten).
3. „Explorer Access“ beantragen. Explorer erlaubt echte Konten mit 2.880 Operationen pro Tag. Das Cockpit braucht
   pro Nacht rund 10 bis 15 Operationen, Backfill einmalig etwa 40. Mehr ist nicht nötig.
4. Falls Google später „Basic“ nahelegt: dafür ist eine Markenverifizierung des Cloud-Projekts Pflicht; die Prüfung
   läuft laut Google automatisiert in Minuten. Für uns optional.
5. Unter „IAM“ prüfen, dass yellowboatschulung@gmail.com Inhaber/Bearbeiter ist; Google schickt Pflichtmitteilungen an diese Adressen.

### A3. OAuth-Zugangsdaten erzeugen

1. „APIs und Dienste“ → „OAuth-Zustimmungsbildschirm“ (bzw. „Google Auth Platform“): Nutzertyp „Extern“, App-Name
   „SEA-Cockpit Copilotenschule“, Support-E-Mail yellowboatschulung@gmail.com. Veröffentlichungsstatus:
   „In Produktion“ setzen (sonst laufen Refresh-Tokens nach 7 Tagen ab). Keine Verifizierung nötig, solange nur
   das eigene Konto zustimmt; die Warnung „nicht verifiziert“ beim Zustimmen einfach bestätigen.
2. „Anmeldedaten“ → „Anmeldedaten erstellen“ → „OAuth-Client-ID“ → Typ „Desktop-App“. Name: `sea-cockpit-cli`.
3. Client-ID und Client-Secret kopieren.

### A4. Refresh-Token erzeugen (einmalig, am Mac)

```bash
cd sea-cockpit && source .venv/bin/activate
pip install google-auth-oauthlib
GOOGLE_OAUTH_CLIENT_ID=<Client-ID> GOOGLE_OAUTH_CLIENT_SECRET=<Secret> python -m cockpit.cli google-auth
```

Der Browser öffnet sich, mit yellowboatschulung@gmail.com anmelden, Zugriff auf Google Ads bestätigen. Das Skript
gibt `GOOGLE_OAUTH_REFRESH_TOKEN=…` aus.

### A5. In die .env auf dem Server eintragen

```
GOOGLE_ADS_ENABLED=1
GOOGLE_ADS_CUSTOMER_ID=7483422182
GOOGLE_ADS_LOGIN_CUSTOMER_ID=          # leer, solange kein Manager-Konto genutzt wird
GOOGLE_OAUTH_CLIENT_ID=…
GOOGLE_OAUTH_CLIENT_SECRET=…
GOOGLE_OAUTH_REFRESH_TOKEN=…
GOOGLE_ADS_DEVELOPER_TOKEN=unused      # Platzhalter, die Bibliothek verlangt einen Wert
```

### A6. Prüfen und Historie laden

```bash
python -m cockpit.cli test-connections      # muss „Verbunden: Yellow-Boat Consulting (7483422182), EUR, …“ zeigen
python -m cockpit.cli backfill --days 120   # Historie seit Kampagnenstart
python -m cockpit.cli purge-demo            # Demo-Daten raus, DEMO_MODE=0 in .env
python -m cockpit.cli nightly               # erster echter Lauf mit Alerts
```

Typische Fehler: `CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION` → Explorer-Zugang noch nicht genehmigt.
`USER_PERMISSION_DENIED` → das angemeldete Google-Konto hat keinen Zugriff auf 748-342-2182 (in Google Ads unter
Verwaltung → Zugriff prüfen). `invalid_grant` → Refresh-Token abgelaufen (Zustimmungsbildschirm steht noch auf „Testen“).

## Teil B: Microsoft Advertising API (Bing)

### B1. Developer-Token

1. Mit einem Nutzer, der im Microsoft-Advertising-Konto **Super Admin** ist, https://ads.microsoft.com öffnen.
2. Einstellungen → Entwicklereinstellungen (früher developers.ads.microsoft.com/Account) → „Token anfordern“.
   Es wird ein universeller Token angelegt; ein Token pro Anwendung reicht.
3. Token kopieren → `MS_ADS_DEVELOPER_TOKEN`.

### B2. App registrieren (OAuth)

1. https://portal.azure.com → Microsoft Entra ID → App-Registrierungen → „Neue Registrierung“.
   Name `sea-cockpit`, unterstützte Kontotypen: „Konten in einem beliebigen Organisationsverzeichnis und persönliche
   Microsoft-Konten“, Umleitungs-URI (Typ „Mobile und Desktop“): `https://login.microsoftonline.com/common/oauth2/nativeclient`.
2. Anwendungs-ID (Client) kopieren → `MS_ADS_CLIENT_ID`. Unter „Zertifikate und Geheimnisse“ optional ein
   Client-Secret anlegen → `MS_ADS_CLIENT_SECRET` (bei öffentlicher Client-Konfiguration nicht nötig).
3. „API-Berechtigungen“ → „Berechtigung hinzufügen“ → „Von meiner Organisation verwendete APIs“ → „Microsoft Advertising“
   → `ads.manage` (delegiert) → hinzufügen.

### B3. Konto- und Kundennummer

In Microsoft Advertising oben rechts: Konto-ID (`MS_ADS_ACCOUNT_ID`) und Kundennummer (`MS_ADS_CUSTOMER_ID`).

### B4. Refresh-Token

```bash
pip install bingads
MS_ADS_CLIENT_ID=<App-ID> python -m cockpit.cli ms-auth
```

URL öffnen, mit dem Advertising-Nutzer zustimmen, die komplette Weiterleitungs-URL zurück ins Terminal kopieren.
Ausgabe `MS_ADS_REFRESH_TOKEN=…` in die .env, dazu `MS_ADS_ENABLED=1`.

Hinweis: Der Microsoft-Connector ist gegen die SDK-Schnittstelle geschrieben, aber noch nicht gegen das Live-Konto
gelaufen (Kampagnen sind pausiert). Erster Lauf mit `test-connections`, dann `backfill --days 30`; Fehler im Job-Log
unter Admin nachlesen.

## Teil C: Trello (Lead-Wahrheit)

1. https://trello.com/power-ups/admin → Power-Up anlegen → API-Schlüssel → `TRELLO_KEY`.
2. Auf derselben Seite „Token“ generieren → `TRELLO_TOKEN`.
3. Board-ID: Board öffnen, an die URL `.json` anhängen, Feld `id` kopieren → `TRELLO_BOARD_ID`.
4. Listen → Status: Standardzuordnung erkennt Listennamen mit „Eingang/Neu“, „Kontaktiert“, „Angebot“, „Gewonnen“,
   „Verloren“. Abweichende Namen in `TRELLO_LIST_STATUS_MAP=Listenname:status;…`.
5. Kanal je Karte: Label `google`, `microsoft`, `organisch`, … oder Zeile `Kanal: google` in der Beschreibung.
   Auftragswert/Erlös: Zeilen `Auftragswert: 7600` und `Erlös: 1520` in der Beschreibung. `TRELLO_ENABLED=1`.

## Teil D: Bookings-Buchungen per Power Automate

1. Power Automate → Neuer Flow → Trigger „Microsoft Bookings: Wenn ein Termin erstellt wird“ (Bookings-Postfach
   CopilotErstgesprch@yellow-boat.com; Voraussetzung: Bookings-Admin).
2. Aktion „HTTP“: Methode POST, URI `https://<cockpit-adresse>/api/webhooks/booking`, Header
   `Content-Type: application/json` und `X-Webhook-Secret: <BOOKINGS_WEBHOOK_SECRET>`, Body:

```json
{ "id": "@{triggerOutputs()?['body/id']}", "start": "@{triggerOutputs()?['body/startTime']}",
  "customer_name": "@{triggerOutputs()?['body/customerName']}", "customer_email": "@{triggerOutputs()?['body/customerEmail']}",
  "service": "@{triggerOutputs()?['body/serviceName']}", "notes": "@{triggerOutputs()?['body/customerNotes']}" }
```

Feldnamen des Triggers können je Connector-Version leicht abweichen; im Flow-Designer aus der dynamischen
Inhaltsliste wählen. Das Cockpit legt daraus einen Lead der Art „erstgespraech“ an.

## Teil E: Sprachmodell, Teams, E-Mail

- Anthropic (Standard, `LLM_PROVIDER=anthropic`): https://console.anthropic.com → API Keys → Create Key → `ANTHROPIC_API_KEY`.
  Unter Limits ein Monatslimit setzen. Modelle: `claude-sonnet-5-5` (Reports, Chat), `claude-haiku-5-5` (kurze Einordnungen).
  Identitätsföderation (kurzlebige Tokens) funktioniert hier nicht: Alwaysdata ist kein unterstützter Identitätsanbieter.

- Alternativ OpenAI (`LLM_PROVIDER=openai`): https://platform.openai.com → API Keys → neuer Schlüssel → `OPENAI_API_KEY`.
  Unter Billing ein monatliches Limit setzen (z. B. 30 €). Modell: `OPENAI_MODEL=gpt-6.1-terra` (Standard),
  günstiger `gpt-6-luna`.
- Teams: Im Zielkanal → Workflows → „Post to a channel when a webhook request is received“ → URL → `TEAMS_WEBHOOK_URL`.
- E-Mail: Alwaysdata-Postfach als Absender (`SMTP_HOST=smtp-y-b.alwaysdata.net`, Port 587, Benutzer/Passwort des
  Postfachs), Empfänger in `ALERT_EMAIL_TO=chris@…,martin@yellow-boat.com`.
