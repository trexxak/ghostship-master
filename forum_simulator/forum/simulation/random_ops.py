from __future__ import annotations

import math
import random


def poisson(lam: float, rng: random.Random) -> int:
    """Sample from a Poisson distribution using Knuth's algorithm."""
    if lam <= 0:
        return 0
    l = math.exp(-lam)
    k = 0
    p = 1.0
    while p > l:
        k += 1
        p *= rng.random()
    return k - 1


def binomial(n: int, p: float, rng: random.Random) -> int:
    """Sample from a Binomial(n, p) distribution."""
    if n <= 0 or p <= 0:
        return 0
    if p >= 1:
        return n
    successes = 0
    for _ in range(n):
        if rng.random() < p:
            successes += 1
    return successes


def geometric(p: float, rng: random.Random) -> int:
    """Return number of failures before first success for parameter p."""
    if p <= 0:
        return 0
    if p >= 1:
        return 0
    count = 0
    while rng.random() >= p:
        count += 1
    return count
