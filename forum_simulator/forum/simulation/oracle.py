from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
import random
from typing import Sequence


@dataclass
class EnergyProfile:
    """Holds the random energy data for a single tick."""

    rolls: list[int]
    energy: int
    energy_prime: int


def roll_exploding_d6(rng: random.Random) -> list[int]:
    """Return the individual rolls from an exploding d6 sequence."""
    rolls: list[int] = []
    while True:
        roll = rng.randint(1, 6)
        rolls.append(roll)
        if roll < 6:
            break
    return rolls


def modulate_energy(energy: int, moment: datetime) -> int:
    """Apply daily sinusoidal modulation to the base energy value."""
    hour = moment.hour + moment.minute / 60.0
    modulation = 1.0 + 0.3 * math.sin(2 * math.pi * hour / 24.0)
    return int(round(energy * modulation))


def build_energy_profile(moment: datetime, rng: random.Random | None = None) -> EnergyProfile:
    """Produce the energy profile (rolls, sum, modulated sum) for a tick."""
    rng = rng or random
    rolls = roll_exploding_d6(rng)
    energy = sum(rolls)
    energy_prime = modulate_energy(energy, moment)
    return EnergyProfile(rolls=rolls, energy=energy, energy_prime=energy_prime)


def describe_rolls(rolls: Sequence[int]) -> str:
    """Return a compact string for logging/diagnostics."""
    return "+".join(str(r) for r in rolls)
