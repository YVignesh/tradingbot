"""
backtest.py — Strategy Backtester
====================================
Usage:
    python backtest.py --from 2025-01-01 --to 2025-03-31

All settings (strategy, symbols, capital, interval, screener, allocation, risk)
are read from config.json. Only the date range is required on the command line.

Optional CLI overrides:
    --strategy  macd_rsi_trend        (default: config.json strategy.name)
    --symbols   SBIN,RELIANCE         (overrides screener, trades these symbols only)
    --interval  FIVE_MINUTE           (overrides config.json strategy.interval)
    --capital   50000                 (overrides config.json risk.capital)
    --no-tsl                          (disables trailing stop-loss)
    --config    path/to/config.json   (default: config.json)

See CLAUDE.md for full architecture notes and config reference.
"""

from backtest_runtime import main

if __name__ == "__main__":
    main()
