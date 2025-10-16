"""Simulation helper utilities."""

from .oracle import EnergyProfile, build_energy_profile, describe_rolls
from .allocators import Allocation, allocate_actions, compute_registration_count, registration_multiplier

__all__ = [
    "EnergyProfile",
    "Allocation",
    "build_energy_profile",
    "describe_rolls",
    "allocate_actions",
    "compute_registration_count",
    "registration_multiplier",
]
