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
4. **Schreiben und Senden**: allgemeine Partnerschaftsanfrage ohne Pakete und Preise, Details erst bei Interesse. Lokale Firmen werden auf die Region angesprochen. Keine Gedankenstriche, Signatur automatisch
5. **Antworten**: prüft alle 30 min das Postfach. Zu jeder Antwort schreibt die KI einen Antwortvorschlag, legt ihn im Postfach unter "Entwürfe" ab und schickt ihn nach Telegram. Abschicken mit `/senden N`, neu schreiben mit `/neu N Hinweis`. Abwesenheitsnotizen werden erkannt, "Abmelden"/"kein Interesse" landet automatisch auf der Sperrliste

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

## Dashboard

Handyoptimierte Oberfläche auf Port **5090**, erreichbar über das Pi-Dashboard (ÖFFNEN) und die Pi-Control-App.
Login wie beim Pi-Dashboard.

- **Übersicht:** Versandstatus (aktiv/pausiert, Test/Live), heute gesendet, Pausieren/Fortsetzen, Suche starten,
  Kennzahlen (angeschrieben, offene Antworten, Antwortquote, Interesse, Warteschlange), Verlauf der letzten 30 Tage,
  Firmen nach Status, welche Quellen am besten funktionieren
- **Antworten:** Antwortvorschläge lesen, **direkt bearbeiten** und senden, mit Hinweis neu schreiben lassen, verwerfen
- **Gesendet:** alle verschickten Mails mit Suche, Score, Begründung und Quelle
- **Queue:** die nächsten Firmen, einzelne mit einem Tipp aus der Warteschlange nehmen
- **Sperre:** Sperrliste ansehen, Domains/Adressen sperren und entsperren

Aktionen im Dashboard werden zusätzlich in Telegram vermerkt.

```bash
cd ~/steckstergaddeanfragen
git pull
venv/bin/pip install -r requirements.txt
nano .env      # DASHBOARD_AUTH_FILE auf die auth.json des Pi-Dashboards setzen (absoluter Pfad)
sudo cp steckster-sponsor-dashboard.service /etc/systemd/system/   # User/Pfade wie beim Bot-Service prüfen
sudo systemctl daemon-reload
sudo systemctl enable --now steckster-sponsor-dashboard
sudo systemctl restart steckster-sponsor    # damit "Suche starten" aus dem Dashboard greift
```

Im Pi-Dashboard in `apps.json` ergänzen (danach `generate_sudoers.py` neu ausführen):

```json
{"id": "sponsor_dashboard", "name": "Sponsor-Bot Dashboard", "service": "steckster-sponsor-dashboard.service",
 "repo_path": "/home/pi/steckstergaddeanfragen", "url": "http://{host}:5090"}
```

Alternativ statt eigener Zeile: beim bestehenden Eintrag `steckster-sponsor` einfach `"url": "http://{host}:5090"`
ergänzen, dann hat der Bot selbst den ÖFFNEN-Button (das Dashboard muss trotzdem als Dienst laufen).

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
| `/antworten` | offene Antworten von Firmen |
| `/vorschlag N` | Antwortvorschlag zu #N nochmal zeigen |
| `/senden N` | Antwortvorschlag zu #N abschicken (im selben Mailverlauf) |
| `/neu N Hinweis` | Vorschlag neu schreiben, z.B. `/neu 3 kürzer, nur Silber und Gold` |
| `/verwerfen N` | selbst antworten oder ignorieren |

Antwortvorschläge werden nie automatisch verschickt. In den Antworten darf die KI die Pakete aus `profil.json` mit Preis und Leistung nennen, wenn die Firma danach fragt.

## Hinweise

- Absender ist `verwaltung@steckstergadde.de` im Namen von Jonas Weber (Teammanagement & Sponsoring). Der Bot liest das Postfach nur lesend, um Antworten zu erkennen. SPF/DKIM bei IONOS für die Domain prüfen, damit die Mails nicht im Spam landen.
- Rechtlich: Unaufgeforderte Werbemails sind in Deutschland auch an Firmen nach § 7 UWG grundsätzlich einwilligungspflichtig. Individuelle, passgenaue Anfragen an Firmenpostfächer mit Abmeldemöglichkeit halten das Risiko klein, ganz weg ist es nicht. Deshalb auch das moderate Tageslimit.
- Alles läuft kostenlos: DuckDuckGo-Suche ohne Key, Groq Free-Tier (Mistral als Ersatz). KI testen: `venv/bin/python test_llm.py`
