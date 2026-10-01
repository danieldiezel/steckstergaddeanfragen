# Steckster Sponsor-Bot

Sucht selbstständig nach möglichen Trikot- und Teamsponsoren für das Steckster Gadde CS2-Team und schreibt sie vollautomatisch an (Standard: 10 Mails pro Werktag, 9 bis 17 Uhr, zufällig verteilt).

## Ablauf

1. **Suchen** (alle 3 h, rotierend)
   - DACH CS Liga: Teams von den Liga-Seiten → deren Websites → deren Partner-/Sponsorenseiten
   - Größere DACH-Esports-Teams per Suche → deren Sponsoren
   - Lokale Firmen Rhein-Main per Suche
   - Branchen, die zu Esports passen (Peripherie, Energy, Hosting, ...)
2. **Anreichern**: Website + Impressum besuchen, beste Mailadresse wählen (sponsoring@ > marketing@ > info@ ...)
3. **Bewerten**: Die KI (Groq, Mistral als Ersatz) gibt einen Fit-Score 1 bis 10. Ab `MIN_FIT_SCORE` (Standard 6) kommt die Firma in die Warteschlange
4. **Schreiben und Senden**: individuelle Mail je Firma, Sponsoren anderer Teams werden darauf angesprochen, lokale Firmen auf die Region. Keine Gedankenstriche, Signatur und Abmeldehinweis automatisch
5. **Antworten**: prüft alle 30 min das Postfach. Antworten kommen als Telegram-Nachricht, "Abmelden"/"kein Interesse" landet automatisch auf der Sperrliste

Keine Firma und keine Adresse wird zweimal angeschrieben.

## Einrichtung (Raspberry Pi)

```bash
git clone https://github.com/danieldiezel/steckstergaddeanfragen.git ~/steckstergaddeanfragen
cd ~/steckstergaddeanfragen
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env        # Keys, Postfach, Telegram
nano profil.json                          # Kanäle, Paketleistungen, Liga-URLs prüfen!
venv/bin/python -m bot.main --einmal      # Testlauf
sudo cp steckster-sponsor.service /etc/systemd/system/
sudo systemctl enable --now steckster-sponsor
```

**Vor dem Start in `profil.json` prüfen:**
- `kanaele`: eure Reichweite/Kanäle eintragen
- `pakete[].leistung`: die Leistungen pro Paket sind Platzhalter, bitte an das anpassen, was ihr wirklich bietet
- `liga_urls`: die direkte URL eurer Liga-/Tabellenseite(n) auf dachcs.de eintragen (gern mehrere Ligen)
- Suchbegriffe unter `suche_lokal` / `suche_branchen` beliebig erweitern

**Testmodus:** `DRY_RUN=1` (Standard) schickt nichts raus, sondern zeigt jede Mail als Vorschau in Telegram. Wenn die Mails passen: `DRY_RUN=0` setzen und Service neu starten. Die Vorschau-Firmen wandern dann automatisch zurück in die Warteschlange.

## Steuerung über das Pi-Dashboard

Im PI_DASHBOARD gibt es einen eigenen Bereich für den Bot (Status, Pause, Testmodus,
Einstellungen, Mails, Warteschlange, Antworten, Log). Einrichtung steht in dessen README.
Einstellungen aus dem Dashboard haben Vorrang vor der `.env` und greifen ohne Neustart.

## Telegram-Befehle

| Befehl | Wirkung |
|---|---|
| `/status` | Zahlen und heutiger Stand |
| `/pause` / `/weiter` | Versand anhalten / fortsetzen |
| `/letzte` | letzte 5 Mails |
| `/mail N` | kompletter Text der N-letzten Mail |
| `/queue` | nächste Firmen in der Warteschlange |
| `/sperren domain.de` | Firma oder Adresse dauerhaft sperren |
| `/suche` | Suche sofort starten |

## Hinweise

- Absender ist `verwaltung@steckstergadde.de` im Namen von Jonas Weber (Teammanagement & Sponsoring). Der Bot liest das Postfach nur lesend, um Antworten zu erkennen. SPF/DKIM bei IONOS für die Domain prüfen, damit die Mails nicht im Spam landen.
- Rechtlich: Unaufgeforderte Werbemails sind in Deutschland auch an Firmen nach § 7 UWG grundsätzlich einwilligungspflichtig. Individuelle, passgenaue Anfragen an Firmenpostfächer mit Abmeldemöglichkeit halten das Risiko klein, ganz weg ist es nicht. Deshalb auch das moderate Tageslimit.
- Alles läuft kostenlos: DuckDuckGo-Suche ohne Key, Groq Free-Tier (Mistral als Ersatz). KI testen: `venv/bin/python test_llm.py`
