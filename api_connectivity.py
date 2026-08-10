"""Deterministic DNS and model-capability connectivity probes."""
from __future__ import annotations

import socket
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class ModelConnectivityResult:
    """Classified outcome of one model capability probe."""

    status: str
    elapsed_seconds: float
    message: str = ""
    capability: Mapping[str, Any] = field(default_factory=dict)
    hostname: str = ""

    @property
    def successful(self) -> bool:
        """Return whether the model can be used for application evaluation."""
        return self.status in {"compatible", "limited"}


def probe_model_capability(
    config: Mapping[str, Any],
    api_key: str,
    *,
    force: bool = True,
    probe: Callable[..., Mapping[str, Any]] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> ModelConnectivityResult:
    """Run one model capability probe and classify exceptions as data."""
    if probe is None:
        from llm_eval import probe_model_compatibility

        probe = probe_model_compatibility

    started_at = clock()
    try:
        capability = dict(probe(dict(config), api_key, force=force))
    except Exception as exc:
        return ModelConnectivityResult(
            status="error",
            elapsed_seconds=max(0.0, clock() - started_at),
            message=str(exc)[:120],
        )

    elapsed = max(0.0, clock() - started_at)
    try:
        reported_elapsed = float(capability.get("response_time", elapsed))
    except (TypeError, ValueError):
        reported_elapsed = elapsed
    status = str(capability.get("status") or "incompatible")
    if status not in {"compatible", "limited"}:
        status = "incompatible"
    return ModelConnectivityResult(
        status=status,
        elapsed_seconds=reported_elapsed,
        message=str(
            capability.get("message")
            or "模型无法生成程序所需评估格式"
        ),
        capability=capability,
    )


def probe_api_connectivity(
    config: Mapping[str, Any],
    api_key: str,
    *,
    dns_lookup: Callable[[str], Any] = socket.gethostbyname,
    probe: Callable[..., Mapping[str, Any]] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> ModelConnectivityResult:
    """Validate DNS first, then verify the configured model capability."""
    started_at = clock()
    hostname = urlparse(str(config.get("base_url") or "")).hostname or ""
    try:
        if not hostname:
            raise socket.gaierror("missing hostname")
        dns_lookup(hostname)
    except (OSError, TypeError):
        return ModelConnectivityResult(
            status="dns_error",
            elapsed_seconds=max(0.0, clock() - started_at),
            message="域名解析失败",
            hostname=hostname,
        )

    result = probe_model_capability(
        config,
        api_key,
        force=True,
        probe=probe,
        clock=clock,
    )
    return replace(
        result,
        elapsed_seconds=max(0.0, clock() - started_at),
        hostname=hostname,
    )
