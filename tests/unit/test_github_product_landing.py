"""Static contracts for the repository-native GitHub Pages landing page."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = ROOT / "docs"
INDEX_PATH = DOCS_DIR / "index.html"
CSS_PATH = DOCS_DIR / "site.css"
README_PATH = ROOT / "README.md"
PRODUCT_HOME_URL = "https://yaoyouzhong.github.io/boss-resume-filter/"


class _LandingParser(HTMLParser):
    """Collect the small set of HTML attributes covered by landing-page tests."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.tags.append((tag, {key: value or "" for key, value in attrs}))


def _parse_landing() -> tuple[str, _LandingParser]:
    html = INDEX_PATH.read_text(encoding="utf-8")
    parser = _LandingParser()
    parser.feed(html)
    return html, parser


def test_github_product_landing_has_complete_local_assets_and_navigation() -> None:
    html, parser = _parse_landing()
    assert '<meta charset="utf-8">' in html.lower()
    assert not any(tag == "script" for tag, _attrs in parser.tags)

    ids = {attrs["id"] for _tag, attrs in parser.tags if attrs.get("id")}
    required_ids = {"main", "top", "demo", "workflow", "capabilities", "privacy", "download"}
    assert required_ids <= ids

    local_references: set[Path] = set()
    for tag, attrs in parser.tags:
        attribute = "src" if tag == "img" else "href" if tag in {"a", "link"} else ""
        value = attrs.get(attribute, "")
        if not value:
            continue
        if value.startswith("#"):
            assert value[1:] in ids
            continue
        parsed = urlparse(value)
        if parsed.scheme or parsed.netloc:
            continue
        local_path = (DOCS_DIR / unquote(parsed.path)).resolve()
        local_path.relative_to(DOCS_DIR.resolve())
        local_references.add(local_path)

    assert local_references
    assert all(path.is_file() for path in local_references)
    assert (DOCS_DIR / ".nojekyll").is_file()


def test_github_product_landing_images_are_accessible_and_aspect_safe() -> None:
    _html, parser = _parse_landing()
    images = [attrs for tag, attrs in parser.tags if tag == "img"]

    assert len(images) == 4
    for attrs in images:
        assert attrs.get("alt", "").strip()
        assert int(attrs["width"]) > 0
        assert int(attrs["height"]) > 0

    css = CSS_PATH.read_text(encoding="utf-8")
    assert "object-fit: contain" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert ".demo-motion" in css
    assert ".demo-static" in css


def test_github_product_landing_states_product_and_data_boundaries() -> None:
    html, _parser = _parse_landing()

    for required_copy in (
        "合成数据示意",
        "默认本机处理",
        "AI 按需开启",
        "联系前人工确认",
        "一人一项待办",
        "中断后可恢复",
    ):
        assert required_copy in html

    assert "releases/latest" in html
    assert "下载最新版" in html


def test_readme_leads_with_the_product_homepage_entry() -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    first_content_line = next(line for line in readme.splitlines() if line.strip())

    assert PRODUCT_HOME_URL in first_content_line
    assert "进入产品主页" in first_content_line
    assert readme.index(PRODUCT_HOME_URL) < readme.index('<h1 align="center">')
