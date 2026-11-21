#!/usr/bin/env python3
"""gta6-trophy-rarity-forecast-01 variant A: adaptive trend + tier prior.

Median: 0.6 * (damped log-time trend of the trophy's logit rate, fitted on
the case's own history) + 0.4 * (horizon-scaled tier prior), blended in
logit space. The prior is a week-1 guess per trophy class (story chapter,
final mission, easy activity, grindy, platinum); it is scaled toward the
last read by w = ln(1+h)/ln(1+108h), so a 90-minute practice case barely
moves off persistence while the 4.5-day final leans on the prior.
Spread: 0.6 * sqrt(horizon_days) logit units.

Pure standard library. Deterministic: no clock, no randomness.
"""
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.645, -0.674, 0.0, 0.674, 1.645]
SPREAD_SIGMA = 0.6


def clamp(x):
    return min(99.99, max(0.01, x))


def logit(pct):
    p = clamp(pct) / 100.0
    return math.log(p / (1.0 - p))


def inv_logit(lg):
    lg = max(-12.0, min(12.0, lg))
    return 100.0 / (1.0 + math.exp(-lg))


def round4(x):
    scaled = x * 10000
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 10000
    return int(value) if value == int(value) else value


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def trophy_prior(trophy):
    """Week-1 (108h) median guess, in percent, from the trophy's class."""
    name = (trophy.get("name") or "").lower()
    desc = (trophy.get("description") or "").lower()
    tier = trophy.get("tier")
    if tier == "platinum":
        return 0.2
    if "gone fishing" in desc:
        return 99.5
    if "complete chapter 1" in desc:
        return 50.0
    if "complete chapter 2" in desc:
        return 35.0
    if "complete chapter 3" in desc:
        return 25.0
    if "complete chapter 4" in desc:
        return 18.0
    if "complete the final mission" in desc:
        return 6.0
    if "100% completion" in desc:
        return 0.5
    if "250 stunts" in desc:
        return 0.3
    if "8 mission strands" in desc:
        return 0.8
    if "3 collectible card sets" in desc:
        return 0.5
    if "100 locations" in desc:
        return 0.5
    if "50 challenges" in desc:
        return 0.3
    if "every spot" in desc:
        return 0.5
    if "every location" in desc:
        return 0.5
    if "5 bank vaults" in desc:
        return 0.5
    if "laundering" in desc:
        return 0.3
    if "all garage properties" in desc:
        return 0.5
    if "20 different races" in desc:
        return 0.8
    if "complete the final mission for" in desc:
        return 15.0
    if desc.startswith("complete ") and ("mission" in desc or "heist" in desc
                                         or "run" in desc or "takedown" in desc
                                         or "play" in desc or "headliners" in desc
                                         or "operation" in desc):
        return 15.0
    if "try each type" in desc:
        return 20.0
    if "arcade game" in desc:
        return 20.0
    if "discover 100 vehicles" in desc:
        return 12.0
    if "beat cal" in desc:
        return 15.0
    if "explore all of leonida" in desc:
        return 8.0
    if tier == "gold":
        return 1.0
    if tier == "silver":
        return 2.0
    return 12.0


def ols(xs, ys):
    """Least-squares line; returns (intercept, slope) or (None, None)."""
    n = len(xs)
    if n < 2:
        return None, None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-12:
        return None, None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    return my - slope * mx, slope


def forecast_case(case):
    if case["levels"] != LEVELS:
        raise ValueError("levels differ")
    history = case["history"]
    if not history:
        raise ValueError("no history")
    t0 = parse_time(history[0]["at"])
    horizon_h = case["horizon_minutes"] / 60.0
    d_days = case["horizon_minutes"] / 1440.0
    spread = SPREAD_SIGMA * math.sqrt(d_days)
    w_prior = math.log1p(horizon_h) / math.log1p(108.0)
    w_prior = min(1.0, max(0.0, w_prior))
    rows = []
    for i, trophy in enumerate(case["trophies"]):
        xs, ys = [], []
        for read in history:
            rate = read["rates"][i]
            if rate is None:
                continue
            hrs = (parse_time(read["at"]) - t0).total_seconds() / 3600.0
            xs.append(math.log1p(hrs))
            ys.append(logit(rate))
        r_last = history[-1]["rates"][i]
        if r_last is None:
            r_last = next((r["rates"][i] for r in reversed(history)
                           if r["rates"][i] is not None), 0.0)
        lg_last = logit(r_last)
        # Trend: damped log-time extrapolation of the logit's own history.
        inter, slope = ols(xs, ys)
        if inter is None:
            lg_trend = lg_last
        else:
            x_tgt = math.log1p(horizon_h + (parse_time(case["at"]) - t0).total_seconds() / 3600.0)
            lg_trend = inter + 0.8 * slope * x_tgt
            lg_trend = max(lg_last - 3.0, min(lg_last + 3.0, lg_trend))
        # Horizon-scaled prior.
        prior = trophy_prior(trophy)
        lg_prior_h = lg_last + w_prior * (logit(prior) - lg_last)
        lg_med = 0.6 * lg_trend + 0.4 * lg_prior_h
        rows.append({
            "id": trophy["id"],
            "q": [round4(clamp(inv_logit(lg_med + z * spread))) for z in Z],
        })
    return {"trophies": rows}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case)
        (OUTPUT_DIR / (path.stem + ".out")).write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
