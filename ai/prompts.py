"""
ai/prompts.py — Prompt Templates with Dynamic Enhancement
============================================================
Base prompts for each AI window (pre-market, mid-day, post-market).
Enhanced at runtime with lessons, rules, and news context.
"""

from __future__ import annotations

# ──────────────────────────────────────────────────────────────
# PRE-MARKET prompt — strategy + symbol + param selection
# ──────────────────────────────────────────────────────────────

PRE_MARKET_SYSTEM = """You are a quantitative trading strategist for the Indian stock market (NSE).
You are configuring a trading bot for today's session.

Your job: Given market context, yesterday's results, recent lessons, and screener picks,
recommend the best trading configuration for today.

AVAILABLE STRATEGIES (choose ONE):
- ema_crossover: EMA 9/21 crossover. Best in trending markets with clear direction.
- macd_rsi_trend: Multi-indicator trend confirmation. Best in moderate trends.
- vwap_pullback: Intraday mean reversion to VWAP. Best in range-bound markets.
- bollinger_breakout: Volatility squeeze breakout. Best after low-volatility periods.
- supertrend: ATR-based trend following. Best in strong, sustained trends.
- rsi_reversal: Mean reversion from extreme RSI. Best in choppy/range-bound markets.
- stochastic_crossover: Oscillator crossover. Best in choppy markets.
- three_ema_trend: Triple EMA alignment. Best in strong directional trends.
- inside_bar: Breakout of compressed ranges. Best after consolidation.
- macd_divergence: Divergence detection. Best at potential reversal points.
- gap_and_go: Gap trading. Best on volatile open days with clear gaps.

DECISION FRAMEWORK:
- ADX > 25 → trending → prefer supertrend, ema_crossover, three_ema_trend
- ADX < 20 → choppy → prefer rsi_reversal, vwap_pullback, stochastic_crossover
- ATR% > 1.5% → high vol → prefer bollinger_breakout, inside_bar
- ATR% < 0.5% → low vol → prefer inside_bar, bollinger_breakout (squeeze)
- Special day (expiry, RBI) → widen SL, reduce trades

PARAMETER GUIDANCE:
- sl_atr_multiplier: 0.5–3.0 (1.5 is standard; widen on volatile days)
- tp_atr_multiplier: 1.0–5.0 (3.0 is standard; ratio of 1:2 SL:TP minimum)
- max_risk_pct: 0.5–3.0 (1.5 is conservative; lower on uncertain days)
- Always maintain TP >= 2.0 × SL for positive expectancy

SYMBOL RULE: symbols_to_prefer and symbols_to_avoid must use the EXACT symbol strings
from the "Screener picks" section below (e.g. "SBIN-EQ", "RELIANCE-EQ").
Do NOT use company abbreviations (RIL, HDFC) or NSE tickers without the -EQ suffix.
Only reference symbols that appear in the screener picks list.

OUTPUT: Return ONLY a JSON object:
{
  "strategy": "<name>",
  "symbols_to_prefer": ["<exact-symbol-from-screener>"],
  "symbols_to_avoid": ["<exact-symbol-from-screener>"],
  "risk_params": {
    "sl_atr_multiplier": <float>,
    "tp_atr_multiplier": <float>,
    "max_risk_pct": <float>
  },
  "confidence": <0-100>,
  "reasoning": "<2-3 sentences>"
}"""


def build_pre_market_prompt(
    *,
    news_block: str,
    lessons_block: str,
    rules_block: str,
    yesterday_stats: dict,
    screener_picks: list[dict],
    regime_state: dict,
    current_config: dict,
    available_strategies: list[str],
) -> str:
    """Build the pre-market user prompt with all context."""
    parts = []

    # Market context
    parts.append("=== MARKET CONTEXT ===")
    adx = regime_state.get("adx", 0)
    atr_pct = regime_state.get("atr_pct", 0)
    regime = regime_state.get("regime", "UNKNOWN")
    parts.append(f"Market regime: {regime} (ADX={adx:.1f}, ATR%={atr_pct:.2f}%)")

    if news_block:
        parts.append(f"\n{news_block}")

    # Yesterday's performance
    parts.append("\n=== YESTERDAY'S RESULTS ===")
    if yesterday_stats:
        parts.append(f"Strategy: {yesterday_stats.get('strategy', '?')}")
        parts.append(f"P&L: ₹{yesterday_stats.get('day_pnl', 0):.0f}")
        parts.append(f"Trades: {yesterday_stats.get('total_trades', 0)}")
        parts.append(f"Win rate: {yesterday_stats.get('win_rate', 0):.0f}%")
        parts.append(f"Avg MAE: ₹{yesterday_stats.get('avg_mae', 0):.2f}")
        parts.append(f"Avg MFE: ₹{yesterday_stats.get('avg_mfe', 0):.2f}")
    else:
        parts.append("(No trading data from yesterday)")

    # Recent lessons
    if lessons_block:
        parts.append(f"\n=== RECENT LESSONS (last 7 days) ===\n{lessons_block}")

    # Accumulated rules
    if rules_block:
        parts.append(f"\n=== {rules_block}")

    # Screener picks
    parts.append("\n=== TODAY'S SCREENER PICKS ===")
    if screener_picks:
        for p in screener_picks[:10]:
            parts.append(
                f"  {p.get('symbol', '?')}: score={p.get('score', 0):.1f} "
                f"close=₹{p.get('close', 0):.0f} atr=₹{p.get('atr', 0):.1f}"
            )
    else:
        parts.append("(Screener not run yet — use default symbol)")

    # Current config (so AI knows what to change FROM)
    parts.append("\n=== CURRENT CONFIG ===")
    risk = current_config.get("risk", {})
    parts.append(f"SL ATR mult: {risk.get('sl_atr_multiplier', 0)}")
    parts.append(f"TP ATR mult: {risk.get('tp_atr_multiplier', 0)}")
    parts.append(f"Max risk %: {risk.get('max_risk_pct', 0)}")
    parts.append(f"Strategy: {current_config.get('strategy', {}).get('name', '?')}")
    parts.append(f"\nAvailable strategies: {', '.join(available_strategies)}")

    parts.append("\nWhat is the best configuration for today?")
    return "\n".join(parts)


# ──────────────────────────────────────────────────────────────
# MID-DAY prompt — review morning, adjust params
# ──────────────────────────────────────────────────────────────

MID_DAY_SYSTEM = """You are reviewing a trading bot's morning performance for the Indian market.
It is approximately 12:30 IST. Analyze the morning trades and recommend adjustments
for the afternoon session (12:30–15:15 IST).

WHAT YOU CAN ADJUST:
- sl_atr_multiplier: widen if morning had too many whipsaws, tighten if losses are large
- tp_atr_multiplier: reduce if winners are giving back gains, increase if trend is strong
- max_risk_pct: reduce if morning was choppy, increase if signals are clean
- symbols_to_drop: remove symbols that generated multiple losing trades

WHAT YOU MUST NOT DO:
- Change strategy mid-day (causes state corruption)
- Add new symbols (no warmup data)
- Force-close existing positions
- Set any parameter outside safe bounds

SYMBOL RULE: symbols_to_drop must use the EXACT symbol strings from the active symbols
list provided below (e.g. "SBIN-EQ"). Do NOT use abbreviations or bare tickers.

OUTPUT: Return ONLY a JSON object:
{
  "param_changes": {"<param>": <value>, ...},
  "symbols_to_drop": ["<exact-active-symbol>"],
  "hold_positions": true,
  "reasoning": "<2-3 sentences>"
}
If no changes needed, return empty param_changes: {}"""


def build_mid_day_prompt(
    *,
    day_plan: dict,
    trades_so_far: list[dict],
    current_params: dict,
    active_symbols: list[str],
    regime_state: dict,
) -> str:
    """Build the mid-day review prompt."""
    parts = []

    parts.append("=== MORNING PLAN ===")
    parts.append(f"Strategy: {day_plan.get('strategy', '?')}")
    parts.append(f"Confidence: {day_plan.get('confidence', '?')}%")
    parts.append(f"Reasoning: {day_plan.get('reasoning', '?')}")

    parts.append(f"\n=== CURRENT PARAMS ===")
    for k, v in current_params.items():
        parts.append(f"  {k}: {v}")

    parts.append(f"\n=== REGIME ===")
    parts.append(f"ADX={regime_state.get('adx', 0):.1f}, ATR%={regime_state.get('atr_pct', 0):.2f}%")
    parts.append(f"Regime: {regime_state.get('regime', 'UNKNOWN')}")

    parts.append(f"\n=== MORNING TRADES ({len(trades_so_far)} total) ===")
    total_pnl = 0.0
    symbol_pnl: dict[str, float] = {}
    for i, t in enumerate(trades_so_far, 1):
        pnl = t.get("gross_pnl", 0)
        total_pnl += pnl
        sym = t.get("symbol", "?")
        symbol_pnl[sym] = symbol_pnl.get(sym, 0) + pnl
        result = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "FLAT"
        parts.append(
            f"  #{i}: {t.get('direction', '?')} {sym} "
            f"P&L=₹{pnl:.2f} ({result}) "
            f"MAE=₹{t.get('mae', 0):.2f} MFE=₹{t.get('mfe', 0):.2f}"
        )

    if not trades_so_far:
        parts.append("  (No trades executed in the morning session)")

    parts.append(f"\nMorning P&L: ₹{total_pnl:.2f}")
    if symbol_pnl:
        parts.append("Per-symbol P&L:")
        for sym, pnl in sorted(symbol_pnl.items(), key=lambda x: x[1]):
            parts.append(f"  {sym}: ₹{pnl:.2f}")

    parts.append(f"\nActive symbols: {', '.join(active_symbols)}")
    parts.append("\nShould any parameters be adjusted for the afternoon?")
    return "\n".join(parts)


# ──────────────────────────────────────────────────────────────
# POST-MARKET prompt — full review + lesson extraction
# ──────────────────────────────────────────────────────────────

POST_MARKET_SYSTEM = """You are a professional trading coach doing a post-market review.
Analyze the full day's trading and extract specific, actionable lessons.

Your goals:
1. Identify what worked and what didn't
2. Extract patterns (e.g. "entries after 13:00 had lower win rate")
3. Suggest specific parameter changes for tomorrow
4. Propose rules that should be tested (if a pattern repeats 3+ days, it becomes permanent)

IMPORTANT: Be specific and quantitative. "Trades were bad" is useless.
"3 out of 4 losses had MAE > 2× SL distance, suggesting SL is too tight" is useful.

OUTPUT: Return ONLY a JSON object:
{
  "day_pnl": <float>,
  "win_rate": <float>,
  "total_trades": <int>,
  "lessons": [
    "<specific lesson 1>",
    "<specific lesson 2>"
  ],
  "param_suggestions": {
    "<param>": <value>
  },
  "rules_to_add": [
    "<specific rule to test>"
  ],
  "strategy_assessment": "<1 sentence on strategy fit for today's market>",
  "mid_day_changes_helped": <true|false|null>,
  "tomorrow_focus": "<one specific thing to prioritize tomorrow>"
}"""


def build_post_market_prompt(
    *,
    day_plan: dict,
    mid_day_adjustments: dict,
    all_trades: list[dict],
    regime_state: dict,
    current_params: dict,
) -> str:
    """Build the post-market review prompt."""
    parts = []

    parts.append("=== TODAY'S PLAN ===")
    parts.append(f"Strategy: {day_plan.get('strategy', '?')}")
    parts.append(f"Confidence: {day_plan.get('confidence', '?')}%")
    parts.append(f"Reasoning: {day_plan.get('reasoning', '?')}")

    parts.append(f"\n=== MID-DAY ADJUSTMENTS ===")
    if mid_day_adjustments and mid_day_adjustments.get("param_changes"):
        for k, v in mid_day_adjustments.get("param_changes", {}).items():
            parts.append(f"  Changed {k} → {v}")
        parts.append(f"  Reason: {mid_day_adjustments.get('reasoning', '?')}")
    else:
        parts.append("  (No mid-day changes were made)")

    parts.append(f"\n=== FINAL PARAMS ===")
    for k, v in current_params.items():
        parts.append(f"  {k}: {v}")

    parts.append(f"\n=== REGIME ===")
    parts.append(f"ADX={regime_state.get('adx', 0):.1f}, ATR%={regime_state.get('atr_pct', 0):.2f}%")

    parts.append(f"\n=== ALL TRADES ({len(all_trades)} total) ===")
    total_pnl = 0.0
    winners = 0
    total_mae = 0.0
    total_mfe = 0.0
    for i, t in enumerate(all_trades, 1):
        pnl = t.get("gross_pnl", 0)
        total_pnl += pnl
        if pnl > 0:
            winners += 1
        mae = t.get("mae", 0)
        mfe = t.get("mfe", 0)
        total_mae += mae
        total_mfe += mfe

        result = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "FLAT"
        entry_time = t.get("entry_time", "?")
        parts.append(
            f"  #{i}: {t.get('direction', '?')} {t.get('symbol', '?')} "
            f"at {entry_time} "
            f"Entry=₹{t.get('entry_price', 0):.2f} Exit=₹{t.get('exit_price', 0):.2f} "
            f"P&L=₹{pnl:.2f} ({result}) "
            f"MAE=₹{mae:.2f} MFE=₹{mfe:.2f}"
        )

    if all_trades:
        n = len(all_trades)
        wr = winners / n * 100
        parts.append(f"\nSummary: P&L=₹{total_pnl:.2f} | Win rate={wr:.0f}% ({winners}/{n})")
        parts.append(f"Avg MAE=₹{total_mae/n:.2f} | Avg MFE=₹{total_mfe/n:.2f}")
    else:
        parts.append("\n(No trades today)")

    parts.append("\nAnalyze the day and extract lessons for tomorrow.")
    return "\n".join(parts)
