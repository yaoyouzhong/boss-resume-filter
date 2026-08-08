"""Pure layout policies shared by the GUI and its environment matrix."""
from __future__ import annotations


RESULT_BASE_COLUMNS = (
    "name",
    "gender",
    "exp",
    "salary",
    "skills",
    "score",
    "ai_eval",
    "level",
    "status",
)
RESULT_EXTRA_COLUMNS = ("age", "education", "job_status")
RESULT_WIDE_COLUMNS = ("school", "company")
RESULT_ALL_COLUMNS = RESULT_BASE_COLUMNS + RESULT_EXTRA_COLUMNS + RESULT_WIDE_COLUMNS


def result_display_columns(
    tree_width: int,
    *,
    maximized: bool,
) -> tuple[str, ...]:
    """Return all ordered result columns; narrow windows use horizontal scroll."""
    _ = max(0, int(tree_width or 0)), bool(maximized)
    return RESULT_ALL_COLUMNS
