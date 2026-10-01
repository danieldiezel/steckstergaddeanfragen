"""Schnelltest: venv/bin/python test_mistral.py"""
import requests
from bot import config

key = config.MISTRAL_API_KEY
print("Key geladen:", f"{key[:4]}...{key[-4:]} ({len(key)} Zeichen)" if key else "NEIN, .env prüfen")
r = requests.post("https://api.mistral.ai/v1/chat/completions",
                  headers={"Authorization": f"Bearer {key}"},
                  json={"model": config.MISTRAL_MODEL,
                        "messages": [{"role": "user", "content": "Sag nur: ok"}]},
                  timeout=30)
print("Status:", r.status_code)
print(r.text[:500])
