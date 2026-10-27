# ChatGPT-Analyse (Chris, Oktober 2026): Klickoptimierung vs. Conversion-Optimierung

Quelle: ChatGPT-Workspace der Copilotenschule, Auswertung „Conversion-Optimierung vergleichen“. Bereinigt um
fünf Test-Conversions vom 24.08.2026, die ursprünglich als echte Conversions gezählt wurden.

## Bereinigter Vergleich

| Kennzahl | Klickoptimierung (01.07.–31.08.) | Conversion-Optimierung (02.09.–13.10.) |
|---|---|---|
| Tage | 62 | 42 |
| Werbekosten | 1.505,12 € | 932,73 € |
| Klicks | 398 | 143 |
| Impressionen | 7.835 | 1.901 |
| CTR | 5,08 % | 7,52 % |
| Ø CPC | 3,78 € | 6,52 € |
| Echte Conversions | 4 | 4 |
| Bereinigte Conversion-Rate | 1,01 % | 2,80 % |
| Kosten pro echter Conversion | 376,28 € | 233,18 € |

Annahme der Analyse: Die vier Conversions im neueren Zeitraum sind echt und die Conversion-Ziele vergleichbar.

## Schlussfolgerungen der Analyse

- Conversion-Optimierung liefert weniger, aber besser konvertierende Klicks: CTR und CVR deutlich höher, CPC +72 %, CPL −38 %.
- Kausalität nicht belegt: unterschiedliche Saison, Nachfrage, Wettbewerb; insgesamt nur acht echte Conversions.
- Die fünf Test-Conversions vom 24.08. könnten das Lernverhalten von Smart Bidding im September beeinflusst haben,
  falls sie als primäre Conversions erfasst und nicht in Google Ads korrigiert wurden. Offen: wurden sie nur
  rechnerisch herausgerechnet oder per Conversion-Anpassung entfernt?
- Empfehlung: bei „Conversions maximieren“ bleiben. Drei Prüfpunkte: (1) Test-Conversions aus der Gebotsgrundlage
  entfernen, (2) Qualität der vier aktuellen Conversions prüfen (echte Anfragen/Terminbuchungen?), (3) Budget und
  Auslieferung: 25 € Tagesbudget, aber nur ca. 22 €/Tag ausgegeben im Zeitraum 02.09.–13.10.; Ursache klären
  (Suchnachfrage, Anzeigenrang, andere Beschränkungen).
- Datengrundlage bleibt dünn: ca. drei Conversions pro Monat sind wenig für Smart Bidding.

## Echte Kontaktanfragen (Kontaktformular, laut Excel „Conversions SEA“)

Zeitraum 20.08.–23.10.2026: neun Kontaktanfragen über das Formular (alle mit Trello-Karte), darunter ein Abschluss
(Auftragswert 7.600 €, bislang realisierter Erlös 1.520 €). Namen und Firmen liegen in Trello, nicht hier.

## ChatGPT-Konzept „KI-Assistent Google Ads“ (Chris, Oktober 2026)

Chris hat mit ChatGPT ein Konzept für einen KI-Assistenten mit Freigabeprozess entworfen (n8n + OpenAI + Google Ads
API, Phase 1 nur lesend). Festgelegte Berechtigungsstufen aus diesem Konzept, die auch für das Cockpit gelten:
Analysieren und Auffälligkeiten melden automatisch; Suchbegriffe ausschließen, Keywords pausieren, Anzeigentexte
ändern, Gebotsstrategie wechseln und Budget erhöhen nur mit Freigabe durch einen Menschen. Wichtigste Kennzahl:
Kosten pro qualifizierter Kundenanfrage, nicht der CPC. Das Cockpit setzt diese Linie um, indem es ausschließlich
vorschlägt und nichts selbst ändert.
