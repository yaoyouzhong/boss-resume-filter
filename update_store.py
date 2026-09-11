"""Local update preferences and reminder snapshots; never contains credentials."""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


class UpdateStore:
    """Persist one update snapshot atomically without modifying release templates."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / ".update_state.json"

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._legacy_state()
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                return {}
            return state
        except (OSError, ValueError):
            return {}

    def _legacy_state(self) -> dict[str, Any]:
        """Honor previous no-update/failure cooldowns; old reminders need a fresh check."""
        try:
            state = json.loads((self.path.parent / ".last_update_check").read_text())
            if not isinstance(state, dict):
                return {}
            timestamp = float(state.get("timestamp", 0))
            failures = min(max(int(state.get("fail_count", 0)), 0), 100)
            if state.get("result") == "no_update":
                interval = 4 * 3600
            elif state.get("result") == "failed":
                interval = 900 * 2 ** min(max(failures - 1, 0), 2)
            else:
                return {}
            return {"next_check": min(timestamp + interval, time.time() + interval), "failures": failures}
        except (OSError, ValueError, TypeError, OverflowError):
            return {}

    def save(self, state: dict[str, Any]) -> None:
        """Replace only after the entire new snapshot has reached disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".update-state-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(state, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)
