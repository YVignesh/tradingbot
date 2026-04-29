"""
main.py — AngelOne Trading Bot
================================
Usage:
    python main.py
    python main.py --config path/to/config.json

Pre-market screener (09:00–09:10 IST) re-selects symbols each trading
day when screener.enabled = true in config.json.

See CLAUDE.md for full architecture notes and config reference.
"""

from bot_runtime import main

if __name__ == "__main__":
    main()
