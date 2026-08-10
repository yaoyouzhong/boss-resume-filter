import socket
import threading

from browser_connection import (
    classify_browser_url,
    connect_browser_address,
    is_debug_port_open,
    probe_page_url,
)


def _is_recommend(url):
    return "/web/chat/recommend" in url or "/web/frame/recommend" in url


def test_browser_url_classification_distinguishes_runtime_states():
    assert classify_browser_url(
        "",
        recommend_matcher=_is_recommend,
    ) == "blank"
    assert classify_browser_url(
        "about:blank",
        recommend_matcher=_is_recommend,
    ) == "blank"
    assert classify_browser_url(
        "https://www.zhipin.com/web/chat/recommend",
        recommend_matcher=_is_recommend,
    ) == "recommend"
    assert classify_browser_url(
        "https://www.zhipin.com/web/geek/chat",
        recommend_matcher=_is_recommend,
    ) == "boss_other"
    assert classify_browser_url(
        "https://example.test/",
        recommend_matcher=_is_recommend,
    ) == "external"


def test_page_url_probe_returns_value_and_exception_as_data():
    class GoodPage:
        url = "https://www.zhipin.com/web/chat/recommend"

    class BrokenPage:
        @property
        def url(self):
            raise RuntimeError("disconnected")

    good = probe_page_url(GoodPage(), timeout=0.1)
    broken = probe_page_url(BrokenPage(), timeout=0.1)

    assert good.url.endswith("/recommend")
    assert good.error is None
    assert broken.url == ""
    assert isinstance(broken.error, RuntimeError)


def test_page_url_probe_enforces_timeout():
    release = threading.Event()

    class BlockingPage:
        @property
        def url(self):
            release.wait(1)
            return "about:blank"

    result = probe_page_url(BlockingPage(), timeout=0.01)
    release.set()

    assert result.timed_out is True
    assert isinstance(result.error, TimeoutError)


def test_debug_port_probe_closes_successful_connection_and_rejects_bad_address():
    class Connection:
        closed = False

        def close(self):
            self.closed = True

    connection = Connection()
    calls = []

    def connector(address, *, timeout):
        calls.append((address, timeout))
        return connection

    assert is_debug_port_open(
        "127.0.0.1:9333",
        timeout=0.25,
        connector=connector,
    ) is True
    assert calls == [(('127.0.0.1', 9333), 0.25)]
    assert connection.closed is True
    assert is_debug_port_open("invalid", connector=connector) is False


def test_debug_port_probe_classifies_connection_error():
    def connector(_address, *, timeout):
        raise socket.timeout(f"timed out after {timeout}")

    assert is_debug_port_open(
        "127.0.0.1:9333",
        connector=connector,
    ) is False


def test_browser_connection_prefers_boss_tab_and_validates_page():
    class Options:
        address = ""

        def set_address(self, address):
            self.address = address

    class Page:
        def __init__(self, url):
            self.url = url
            self.validated = False

        def run_js(self, script):
            assert script == "return 1"
            self.validated = True
            return 1

    boss_tab = Page("https://www.zhipin.com/web/chat/recommend")
    other_tab = Page("https://example.test")

    class BasePage(Page):
        address = "127.0.0.1:9333"

        def get_tabs(self):
            return [other_tab, boss_tab]

    result = connect_browser_address(
        "127.0.0.1:9333",
        timeout=0.2,
        prefer_boss_tab=True,
        validate_page=True,
        options_factory=Options,
        page_factory=lambda options: BasePage(options.address),
    )

    assert result.connected is True
    assert result.page is boss_tab
    assert result.address == "127.0.0.1:9333"
    assert result.url.endswith("/recommend")
    assert boss_tab.validated is True


def test_browser_connection_classifies_factory_error_and_timeout():
    class Options:
        def set_address(self, _address):
            pass

    error = connect_browser_address(
        "127.0.0.1:9333",
        timeout=0.1,
        options_factory=Options,
        page_factory=lambda _options: (_ for _ in ()).throw(
            RuntimeError("connect failed")
        ),
    )
    assert isinstance(error.error, RuntimeError)
    assert error.connected is False

    release = threading.Event()

    def blocking_factory(_options):
        release.wait(1)
        return object()

    timed_out = connect_browser_address(
        "127.0.0.1:9333",
        timeout=0.01,
        options_factory=Options,
        page_factory=blocking_factory,
    )
    release.set()
    assert timed_out.timed_out is True
    assert timed_out.connected is False
