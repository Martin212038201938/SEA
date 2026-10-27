# SEA-Cockpit: Briefing für Chris

## Was es ist

Eine kleine Web-App (Login mit Passwort), die jede Nacht unser Google-Ads-Konto ausliest und die Zahlen dauerhaft
speichert: Kampagnen, Anzeigengruppen, Keywords, Suchbegriffe, Anzeigen, Änderungsverlauf, Auktionsdaten. Dazu die
echten Leads aus Trello und die Erstgespräche aus Bookings. Microsoft Advertising kommt dazu, sobald die Kampagnen
wieder laufen.

Aus den Daten macht das Cockpit drei Dinge: Es warnt bei Auffälligkeiten (täglich, nur wenn etwas ist), es schreibt
montags einen Wochenreport mit priorisierten Vorschlägen, und es beantwortet im Chat Fragen zu den Zahlen.

Es ändert nichts im Konto. Du entscheidest und setzt um.

## Was du davon hast

- Ein Blick morgens: Was ist passiert, was hat sich verändert, was sollte ich heute prüfen.
- Vorschläge mit Begründung, erwartetem Effekt und Schritten, die davon ausgehen, dass du Google Ads kennst.
- Vergleiche, die bisher mühsam waren: vorher/nachher je Änderung, Zeitraum gegen Zeitraum, mit dem Hinweis,
  wenn andere Änderungen im selben Fenster den Effekt verfälschen.
- Eine Historie, die nicht mehr verloren geht, egal wie Google die Oberfläche umbaut.

## Was du beitragen musst

1. **Umsetzungen melden.** Wenn du einen Vorschlag umgesetzt hast, im Cockpit auf „umgesetzt“ setzen (mit Datum)
   oder im Chat schreiben „Vorschlag 7 gestern umgesetzt“. Erst dadurch kann das Cockpit messen, ob es gewirkt hat.
   Das Cockpit prüft parallel den Änderungsverlauf im Konto und meldet, wenn es die Änderung nicht findet.
2. **Experimente anlegen**, bevor du etwas Größeres änderst: Name, Hypothese, Startdatum, betroffene Kampagnen.
   Danach zeigt das Cockpit laufend den Vergleich zum gleich langen Zeitraum davor.
3. **Leads sauber halten.** Trello-Karten mit Label `google`/`microsoft`/`organisch` versehen (oder Zeile
   `Kanal: google` in der Beschreibung). Bei Abschluss `Auftragswert: 7600` und `Erlös: …` eintragen.
   Nur so stimmt die Rechnung „Kosten je echtem Lead“.
4. **Alerts bestätigen** („Gesehen“), sonst bleiben sie in der Übersicht.

## Wo was ist

| Seite | Inhalt |
|---|---|
| Übersicht | Kennzahlen mit Vergleich zur Vorperiode, Verlauf, offene Vorschläge, Alerts, Leads, laufende Experimente |
| Vorschläge | Alle Vorschläge nach Priorität; Status setzen |
| Experimente | Anlegen, vorher/nachher, Störfaktoren |
| Suchbegriffe / Keywords / Anzeigen | Rohdaten mit Filtern; „nur ohne Conversion“ ist die Negativlisten-Quelle |
| Leads | Trello, Bookings, manuell; Kanal und Werte korrigieren |
| Reports | Wochen- und Monatsreports |
| Chat | Fragen in normaler Sprache, z. B. „Warum ist der CPC seit zwei Wochen höher?“ |

## Was das Cockpit nicht ist

Kein Autopilot, kein Ersatz für den Blick ins Konto, keine Garantie, dass ein Vorschlag wirkt. Bei unter zehn
Conversions sind alle Prozentvergleiche Richtungsangaben, keine Beweise. Das sagt das Cockpit auch selbst.
