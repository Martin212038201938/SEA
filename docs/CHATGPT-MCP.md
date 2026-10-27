# Einbindung im ChatGPT-Team-Workspace (MCP)

Das Cockpit stellt unter `https://<adresse>/mcp/<MCP_TOKEN>/mcp` einen MCP-Server (Streamable HTTP) bereit. Er enthält
dieselben Werkzeuge wie der Cockpit-Chat: Kennzahlen, Zeitreihen, Suchbegriffe, Keywords, Anzeigen, Änderungsverlauf,
Auktionsdaten, Leads, Alerts, Vorschläge, Experimente, Reports, Wissensbasis, Nur-Lese-SQL, sowie das Anlegen von
Experimenten, Setzen des Vorschlagsstatus und manuelle Leads. Nichts davon schreibt in Google Ads.

## Einrichtung

1. Workspace-Admin: ChatGPT → Einstellungen → Connectors (bzw. „Apps & Connectors“) → eigene Connectoren/MCP-Server
   erlauben. Die Bezeichnung und der Ort ändern sich bei OpenAI regelmäßig; gesucht wird die Option, einen
   „Custom connector“ mit einer MCP-Server-URL anzulegen (je nach Stand hinter dem „Developer mode“).
2. Neuen Connector anlegen: Name „SEA-Cockpit“, URL `https://sea.y-b.alwaysdata.net/mcp/<MCP_TOKEN>/mcp`,
   Authentifizierung „Keine“ (das Geheimnis steckt im Pfad). Beschreibung: „Google-Ads-Daten der Copilotenschule“.
3. Im Workspace freigeben, damit Chris ihn in seinen Chats aktivieren kann.
4. Testfrage in ChatGPT mit aktiviertem Connector: „Wie haben sich Kosten und CPL in den letzten 28 Tagen entwickelt?“

## Sicherheit

- Der Pfad enthält das Token; wer die URL kennt, kann lesen und Experimente/Status anlegen. Deshalb: URL nur im
  Workspace hinterlegen, nicht in Chats einfügen. Token rotieren: neuen Wert in `.env`, Site neu starten, Connector-URL anpassen.
- Alternativ akzeptiert der Server `Authorization: Bearer <MCP_TOKEN>` (für Claude Code, eigene Skripte).
- Ausbaustufe OAuth: Das MCP-SDK (`MCPServer(token_verifier=…)`) kann Bearer-Tokens gegen einen Autorisierungsserver
  prüfen. Sinnvoll, sobald mehr als zwei Personen zugreifen.

## Was ChatGPT dann kann

- Fragen wie im Cockpit-Chat beantworten, aber im gewohnten Workspace, auch kombiniert mit anderen Connectoren.
- Vorschläge als umgesetzt markieren („Vorschlag 12 habe ich heute umgesetzt“ → `update_suggestion_status`).
- Experimente anlegen und vergleichen.
- Einen eigenen Systemprompt hat ChatGPT dort nicht; die Server-Instructions geben nur die Grundregeln mit.
  Für konsistente Antworten lohnt sich ein Custom GPT im Workspace mit dem Systemtext aus `cockpit/llm/prompts.py`
  (`CHAT_SYSTEM`) und dem Connector als Werkzeug.

## Claude

Claude Desktop / Claude Code können denselben Endpunkt als Remote-MCP-Server nutzen (URL + Bearer-Token).
