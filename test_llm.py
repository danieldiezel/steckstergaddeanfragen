"""Schnelltest der KI-Anbieter: venv/bin/python test_llm.py"""
from bot import config, llm

print("Reihenfolge:", config.LLM_PROVIDER)
for name in llm.PROVIDERS:
    key = llm.PROVIDERS[name][1]()
    if not key:
        print(f"{name}: kein Key in .env")
        continue
    try:
        res = llm._call(name, [{"role": "user", "content": 'Antworte als JSON: {"status": "ok"}'}], 0)
        print(f"{name}: OK -> {res}")
    except Exception as e:
        print(f"{name}: FEHLER -> {e}")
