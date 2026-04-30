"""
ai/ — 3-Window AI Trading Intelligence
=========================================
Pre-Market (08:50) → Mid-Day (12:30) → Post-Market (15:30)

Modules:
  client.py       — Hardened AIClient (retry, JSON mode, thread-safe)
  orchestrator.py — 3-window coordinator
  prompts.py      — Base prompts + dynamic lesson injection
  lessons.py      — Daily lessons persistence + rule extraction
  news.py         — Market news & event collector (RSS, calendar)
  guardrails.py   — Hard limits, validation, audit logging
"""
