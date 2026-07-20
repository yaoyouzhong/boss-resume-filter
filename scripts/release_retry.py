"""Shared bounded retries for release-process command I/O.

Release scripts use one initial attempt plus three immediate retries.  The
helper deliberately excludes deterministic authentication, authorization,
argument, repository, and non-fast-forward failures from retrying.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from typing import Any


RETRY_DELAYS: tuple[int, ...] = (2, 4, 6)
FILE_RETRY_DELAYS: tuple[float, ...] = (0.2, 0.5, 1.0)
NON_RETRYABLE_MARKERS = (
    "authentication failed",
    "authorization failed",
    "not logged into",
    "http 401",
    "http 403",
    "permission denied",
    "repository not found",
    "unknown flag",
    "unknown option",
    "invalid argument",
    "non-fast-forward",
    "not possible to fast-forward",
    "fetch first",
    "protected branch",
    "http 400",
    "http 404",
    "http 405",
    "http 409",
    "http 410",
    "http 422",
    "unprocessable entity",
    "validation failed",
)
TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "connection reset",
    "connection aborted",
    "temporary",
    "temporarily",
    "try again",
    "rate limit",
    "http 408",
    "http 425",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "head sha can't be blank",
)
SENSITIVE_QUERY_PATTERN = re.compile(
    r"(?i)(access_token|api[_-]?key|apikey|token|secret|password)="
    r"([^&\s\"'<>\)\[\]\}]+)"
)
BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+([^\s,;\"'<>\)\[\]\}]+)")
REDACTED = "[REDACTED]"


class RetryExhausted(RuntimeError):
    """A retryable release I/O operation did not converge."""


def redact_sensitive_text(value: object, secrets: Sequence[str] = ()) -> str:
    """Return diagnostic text with credentials removed before logging."""
    text = str(value)
    text = SENSITIVE_QUERY_PATTERN.sub(
        lambda match: f"{match.group(1)}={REDACTED}",
        text,
    )
    text = BEARER_PATTERN.sub(f"Bearer {REDACTED}", text)
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), REDACTED)
    return text


def command_detail(result: subprocess.CompletedProcess[str]) -> str:
    """Return a compact error message from a completed CLI process."""
    return redact_sensitive_text(
        result.stderr or result.stdout or "command failed"
    ).strip()


def is_retryable_cli_failure(result: subprocess.CompletedProcess[str]) -> bool:
    """Classify a failed remote CLI call, rejecting deterministic failures."""
    if result.returncode == 0:
        return False
    detail = command_detail(result).lower()
    if any(marker in detail for marker in TRANSIENT_MARKERS):
        return True
    return not any(marker in detail for marker in NON_RETRYABLE_MARKERS)


def run_cli_with_retries(
    run: Callable[..., subprocess.CompletedProcess[str]],
    args: list[str],
    label: str,
    *,
    postcondition: Callable[[], bool] | None = None,
    retry_delays: Sequence[float] = RETRY_DELAYS,
) -> subprocess.CompletedProcess[str]:
    """Run remote CLI I/O, checking an idempotent postcondition after failure."""
    last_result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(len(retry_delays) + 1):
        last_result = run(args, check=False, capture_output=True)
        if last_result.returncode == 0:
            return last_result
        if postcondition is not None and postcondition():
            return subprocess.CompletedProcess(
                last_result.args,
                0,
                last_result.stdout,
                last_result.stderr,
            )
        if (
            attempt >= len(retry_delays)
            or not is_retryable_cli_failure(last_result)
        ):
            return last_result
        delay = retry_delays[attempt]
        print(
            f"  [重试] {label}瞬时失败，准备第 {attempt + 1}/"
            f"{len(retry_delays)} 次重试（{delay:g}s 后）"
        )
        time.sleep(delay)
    assert last_result is not None
    return last_result


def run_json_query_with_retries(
    run: Callable[..., subprocess.CompletedProcess[str]],
    args: list[str],
    label: str,
    *,
    retry_delays: Sequence[float] = RETRY_DELAYS,
) -> Any:
    """Run a read-only CLI JSON query, including malformed-output retries."""
    last_error = "unknown"
    for attempt in range(len(retry_delays) + 1):
        result = run(args, check=False, capture_output=True)
        if result.returncode == 0:
            try:
                return json.loads(result.stdout or "null")
            except json.JSONDecodeError as exc:
                last_error = f"JSONDecodeError: {exc}"
        else:
            last_error = command_detail(result)
            if not is_retryable_cli_failure(result):
                raise RetryExhausted(f"{label}失败：{last_error}")
        if attempt >= len(retry_delays):
            break
        delay = retry_delays[attempt]
        print(
            f"  [重试] {label}瞬时失败，准备第 {attempt + 1}/"
            f"{len(retry_delays)} 次重试（{delay:g}s 后）"
        )
        time.sleep(delay)
    raise RetryExhausted(f"{label}失败：{last_error}")
