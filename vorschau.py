"""Zeigt, was der Bot schreiben würde. Verschickt NICHTS und ändert nichts an der Datenbank.

    venv/bin/python vorschau.py        # 1 Mail an die beste Firma in der Warteschlange
    venv/bin/python vorschau.py 3      # die nächsten 3
"""
import sys

from bot import config, db, main

n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 1
db.init()
profile = config.load_profile()
rows = db.next_to_contact(n)
if not rows:
    print("Warteschlange ist leer. Erst Firmen finden und bewerten lassen:")
    print("  venv/bin/python -m bot.main --einmal")
    sys.exit()

for row in rows:
    mail = main._mail_for(row, profile)
    print("=" * 70)
    print(f"Firma:   {row['name']} ({row['domain']})")
    print(f"An:      {row['email']}")
    print(f"Score:   {row['fit_score']}/10, gefunden über: {row['source']} {row['source_detail'] or ''}")
    print(f"Betreff: {mail['betreff']}")
    print("-" * 70)
    print(mail["text"])
print("=" * 70)
print("Nur Vorschau, nichts wurde gesendet.")
