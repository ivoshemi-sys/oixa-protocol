"""
AXON Protocol — Surge Pricing Engine

Dynamic pricing for spot compute capacity based on:
- Time-of-day demand patterns (peak/off-peak)
- Real-time supply/demand ratio
- Capability-specific scarcity
"""

import math
from datetime import datetime, timezone
from typing import Optional


# Time-of-day multipliers (UTC hour → multiplier)
# Peak hours: 14-22 UTC (US business hours + EU afternoon)
_HOURLY_MULTIPLIERS = {
    0:  0.70, 1:  0.65, 2:  0.60, 3:  0.60, 4:  0.65, 5:  0.70,
    6:  0.75, 7:  0.85, 8:  0.95, 9:  1.00, 10: 1.05, 11: 1.10,
    12: 1.10, 13: 1.15, 14: 1.20, 15: 1.25, 16: 1.25, 17: 1.20,
    18: 1.20, 19: 1.15, 20: 1.10, 21: 1.05, 22: 1.00, 23: 0.85,
}

# Demand tier thresholds (active_requests / available_capacity)
_DEMAND_TIERS = [
    (0.0,  0.3,  0.80),  # underdemand  → 20% discount
    (0.3,  0.6,  1.00),  # normal       → base price
    (0.6,  0.8,  1.20),  # busy         → 20% premium
    (0.8,  0.95, 1.50),  # high demand  → 50% premium
    (0.95, 1.0,  2.00),  # near full    → 100% premium  (surge)
    (1.0,  9.9,  3.00),  # overloaded   → 200% premium  (extreme surge)
]

# Hard floor and ceiling for any multiplier output
_MIN_MULTIPLIER = 0.50
_MAX_MULTIPLIER = 5.00


def _time_multiplier() -> float:
    hour = datetime.now(timezone.utc).hour
    return _HOURLY_MULTIPLIERS.get(hour, 1.0)


def _demand_multiplier(demand_ratio: float) -> float:
    for lo, hi, mult in _DEMAND_TIERS:
        if lo <= demand_ratio < hi:
            return mult
    return _DEMAND_TIERS[-1][2]


def calculate_surge_multiplier(
    active_requests: int,
    available_slots: int,
    capability: Optional[str] = None,
) -> float:
    """
    Return the surge multiplier for current conditions.

    Args:
        active_requests: number of pending/in-progress spot requests
        available_slots: total capacity slots offered on the market right now
        capability: optional capability tag for future per-skill adjustments

    Returns:
        float multiplier (e.g. 1.5 = 50% above base price)
    """
    if available_slots <= 0:
        demand_ratio = 1.5  # force extreme surge when nothing available
    else:
        demand_ratio = active_requests / available_slots

    time_mult   = _time_multiplier()
    demand_mult = _demand_multiplier(demand_ratio)

    # Combine multiplicatively, apply soft cap via log compression above 2x
    raw = time_mult * demand_mult
    if raw > 2.0:
        # Soft cap: compress values above 2x using log curve
        excess = raw - 2.0
        raw = 2.0 + math.log1p(excess) * 0.8

    return round(max(_MIN_MULTIPLIER, min(_MAX_MULTIPLIER, raw)), 4)


def apply_surge(base_price_usdc: float, multiplier: float) -> float:
    """Apply multiplier to a base USDC price, rounded to 6 decimal places."""
    return round(base_price_usdc * multiplier, 6)


def get_pricing_context(
    active_requests: int,
    available_slots: int,
) -> dict:
    """
    Return a full pricing context dict suitable for API responses.
    """
    multiplier   = calculate_surge_multiplier(active_requests, available_slots)
    demand_ratio = (active_requests / available_slots) if available_slots > 0 else 1.5

    if multiplier < 0.90:
        label = "off-peak"
    elif multiplier < 1.10:
        label = "normal"
    elif multiplier < 1.40:
        label = "busy"
    elif multiplier < 2.00:
        label = "surge"
    else:
        label = "extreme-surge"

    return {
        "surge_multiplier":  multiplier,
        "surge_label":       label,
        "demand_ratio":      round(demand_ratio, 3),
        "active_requests":   active_requests,
        "available_slots":   available_slots,
        "time_multiplier":   _time_multiplier(),
        "demand_multiplier": _demand_multiplier(demand_ratio),
        "utc_hour":          datetime.now(timezone.utc).hour,
    }
