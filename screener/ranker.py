"""
screener/ranker.py — Candidate ranking
======================================
Ranks screened symbols with a simple momentum + participation score.
"""

from __future__ import annotations


def rank_candidates(candidates: list[dict], top_n: int) -> list[dict]:
    ranked = []
    for candidate in candidates:
        score = (
            float(candidate.get("momentum_5d", 0.0)) * 0.6
            + float(candidate.get("volume_spike", 0.0)) * 25.0
            - float(candidate.get("gap_pct", 0.0)) * 0.5
        )
        enriched = dict(candidate)
        enriched["score"] = round(score, 2)
        ranked.append(enriched)

    ranked.sort(
        key=lambda item: (
            item.get("score", 0.0),
            item.get("momentum_5d", 0.0),
            item.get("volume_spike", 0.0),
        ),
        reverse=True,
    )
    return ranked[:top_n] if top_n > 0 else ranked
