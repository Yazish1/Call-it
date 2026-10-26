"""Numerically stable LMSR pricing helpers."""

import math


def cost(q_yes: float, q_no: float, b: float) -> float:
    """Return LMSR cost using log-sum-exp to avoid overflow."""
    if b <= 0:
        raise ValueError("liquidity b must be positive")
    a = q_yes / b
    c = q_no / b
    maximum = max(a, c)
    return b * (maximum + math.log(math.exp(a - maximum) + math.exp(c - maximum)))


def prices(q_yes: float, q_no: float, b: float) -> tuple[float, float]:
    """Return (YES, NO) prices, which sum to one."""
    if b <= 0:
        raise ValueError("liquidity b must be positive")
    a = q_yes / b
    c = q_no / b
    maximum = max(a, c)
    yes_weight = math.exp(a - maximum)
    no_weight = math.exp(c - maximum)
    yes = yes_weight / (yes_weight + no_weight)
    return yes, 1.0 - yes


def shares_for_cost(q_yes: float, q_no: float, side: str, amount: float,
                    b: float) -> float:
    """Solve the LMSR cost increase for shares with a bounded bisection."""
    side = side.lower()
    if side not in {"yes", "no"}:
        raise ValueError("side must be yes or no")
    if amount <= 0 or b <= 0:
        raise ValueError("amount and b must be positive")
    before = cost(q_yes, q_no, b)
    lo, hi = 0.0, max(amount, b)
    while cost(q_yes + (hi if side == "yes" else 0),
               q_no + (hi if side == "no" else 0), b) - before < amount:
        hi *= 2
    for _ in range(100):
        mid = (lo + hi) / 2
        after = cost(q_yes + (mid if side == "yes" else 0),
                     q_no + (mid if side == "no" else 0), b)
        if after - before < amount:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
