"""Idealized resource-reservation train timetabling prototype.

The prototype deliberately starts with fixed train chains, single-capacity
resources, waiting allowed between every task, discrete time, and mutually
exclusive resource-reservation branches.  It is designed as a transparent
correctness kernel next to the existing RAS DP/LR/B&B implementation.
"""

from .model import (
    Conflict,
    Instance,
    JobSpec,
    ReservationDecision,
    Schedule,
    TaskKey,
    TaskSpec,
    TaskTiming,
)
from .branch_and_bound import ReservationBranchAndBound, BBSummary

__all__ = [
    "Conflict",
    "Instance",
    "JobSpec",
    "ReservationDecision",
    "Schedule",
    "TaskKey",
    "TaskSpec",
    "TaskTiming",
    "ReservationBranchAndBound",
    "BBSummary",
]
