"""Canonical job-name identity shared by storage, GUI and scan workflows."""
from __future__ import annotations

from typing import Any


def normalize_job_name(value: Any) -> str:
    """Return one job identity with all Unicode whitespace removed."""
    return "".join(str(value or "").split())


def job_names_equal(left: Any, right: Any) -> bool:
    """Return whether two display names refer to the same configured job."""
    return normalize_job_name(left).casefold() == normalize_job_name(right).casefold()
