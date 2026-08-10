"""Bounded probes for Chrome debug ports and DrissionPage connections."""
from __future__ import annotations

import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PageUrlProbe:
    """Bounded result of reading one browser page URL."""

    url: str = ""
    error: Exception | None = None
    timed_out: bool = False


@dataclass(frozen=True)
class BrowserConnectionResult:
    """Bounded result of creating and validating a browser page connection."""

    page: Any = None
    address: str = ""
    url: str = ""
    error: Exception | None = None
    timed_out: bool = False

    @property
    def connected(self) -> bool:
        """Return whether a page was obtained without timeout or error."""
        return (
            self.page is not None
            and self.error is None
            and not self.timed_out
        )


def classify_browser_url(
    url: object,
    *,
    recommend_matcher: Callable[[str], bool],
) -> str:
    """Classify a browser URL without reading page or GUI state."""
    normalized = str(url or "").strip()
    if normalized in {"", "about:blank"}:
        return "blank"
    if recommend_matcher(normalized):
        return "recommend"
    lowered = normalized.lower()
    if "zhipin.com" in lowered or "boss" in lowered:
        return "boss_other"
    return "external"


def probe_page_url(page: Any, *, timeout: float = 1.0) -> PageUrlProbe:
    """Read page.url with a hard wait bound."""
    result: dict[str, Any] = {}

    def read_url() -> None:
        try:
            result["url"] = str(getattr(page, "url", "") or "")
        except Exception as exc:
            result["error"] = exc

    worker = threading.Thread(target=read_url, daemon=True)
    worker.start()
    worker.join(timeout=timeout)
    if worker.is_alive():
        return PageUrlProbe(
            error=TimeoutError("browser_page.url 访问超时"),
            timed_out=True,
        )
    error = result.get("error")
    if isinstance(error, Exception):
        return PageUrlProbe(error=error)
    return PageUrlProbe(url=str(result.get("url") or ""))


def is_debug_port_open(
    address: str,
    *,
    timeout: float = 1.0,
    connector: Callable[..., Any] = socket.create_connection,
) -> bool:
    """Return whether a host:port debug endpoint accepts TCP connections."""
    try:
        host, port_text = str(address).rsplit(":", 1)
        connection = connector((host, int(port_text)), timeout=timeout)
        try:
            connection.close()
        except AttributeError:
            pass
        return True
    except (OSError, TypeError, ValueError):
        return False


def connect_browser_address(
    address: str,
    *,
    timeout: float,
    prefer_boss_tab: bool = False,
    validate_page: bool = False,
    options_factory: Callable[[], Any] | None = None,
    page_factory: Callable[[Any], Any] | None = None,
) -> BrowserConnectionResult:
    """Create one DrissionPage connection inside a bounded worker thread."""
    result: dict[str, Any] = {}

    def connect() -> None:
        try:
            local_options_factory = options_factory
            local_page_factory = page_factory
            if local_options_factory is None or local_page_factory is None:
                from DrissionPage import ChromiumOptions, ChromiumPage

                local_options_factory = ChromiumOptions
                local_page_factory = ChromiumPage
            options = local_options_factory()
            options.set_address(address)
            page = local_page_factory(options)
            selected_page = page
            if prefer_boss_tab:
                try:
                    tabs = list(page.get_tabs() or [])
                except Exception:
                    tabs = []
                for tab in tabs:
                    try:
                        if "zhipin.com" in str(tab.url or "").lower():
                            selected_page = tab
                            break
                    except Exception:
                        continue
            if validate_page:
                selected_page.run_js("return 1")
            result["page"] = selected_page
            result["address"] = str(
                getattr(page, "address", "") or address
            )
            result["url"] = str(
                getattr(selected_page, "url", "") or ""
            )
        except Exception as exc:
            result["error"] = exc

    worker = threading.Thread(target=connect, daemon=True)
    worker.start()
    worker.join(timeout=timeout)
    if worker.is_alive():
        return BrowserConnectionResult(
            address=address,
            error=TimeoutError("ChromiumPage 连接超时"),
            timed_out=True,
        )
    error = result.get("error")
    if isinstance(error, Exception):
        return BrowserConnectionResult(address=address, error=error)
    return BrowserConnectionResult(
        page=result.get("page"),
        address=str(result.get("address") or address),
        url=str(result.get("url") or ""),
    )
