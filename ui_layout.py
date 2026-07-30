"""Pure layout policies shared by the GUI and its environment matrix."""
from __future__ import annotations


RESULT_BASE_COLUMNS = (
    "name",
    "exp",
    "salary",
    "skills",
    "score",
    "ai_eval",
    "level",
    "status",
)
RESULT_EXTRA_COLUMNS = ("education", "age", "job_status")
RESULT_WIDE_COLUMNS = ("school", "company")


def result_display_columns(
    tree_width: int,
    *,
    maximized: bool,
) -> tuple[str, ...]:
    """Return the readable result-table column set for one real table width."""
    width = max(0, int(tree_width or 0))
    columns = RESULT_BASE_COLUMNS
    if width >= 1100:
        columns += RESULT_EXTRA_COLUMNS
    if maximized and width >= 1250:
        columns += RESULT_WIDE_COLUMNS
    return columns
