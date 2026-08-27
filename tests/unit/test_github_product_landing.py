"""Static contracts for the repository-native GitHub Pages landing page."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = ROOT / "docs"
INDEX_PATH = DOCS_DIR / "index.html"
CSS_PATH = DOCS_DIR / "site.css"
README_PATH = ROOT / "README.md"
PRODUCT_HOME_URL = "https://yaoyouzhong.github.io/boss-resume-filter/"
USER_GUIDE_PATH = DOCS_DIR / "BOSS招聘系统操作说明-图文版.md"
USER_GUIDE_LINK = "docs/BOSS招聘系统操作说明-图文版.md"
USER_GUIDE_GITHUB_URL = (
    "https://github.com/yaoyouzhong/boss-resume-filter/blob/master/docs/"
    "BOSS%E6%8B%9B%E8%81%98%E7%B3%BB%E7%BB%9F%E6%93%8D%E4%BD%9C%E8%AF%B4%E6%98%8E-"
    "%E5%9B%BE%E6%96%87%E7%89%88.md"
)


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
    assert "项目由 yaoyouzhong 主导设计与开发" in readme
    assert "\u59da\u6709\u5fe0" not in readme


def test_github_home_uses_the_illustrated_guide_and_expected_preview_sequence() -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    landing = INDEX_PATH.read_text(encoding="utf-8")
    guide = USER_GUIDE_PATH.read_text(encoding="utf-8")

    assert "GUI%20使用说明.md" not in readme
    assert readme.count(USER_GUIDE_LINK) == 5
    assert f"{USER_GUIDE_LINK}#十四常见问题处理" in readme
    guide_images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", guide)
    assert len(guide_images) >= 10
    assert all(
        (USER_GUIDE_PATH.parent / unquote(urlparse(reference).path)).is_file()
        for reference in guide_images
    )
    assert USER_GUIDE_GITHUB_URL in landing
    assert "blob/master/GUI%20使用说明.md" not in landing

    job_heading = readme.index("### 岗位配置")
    run_heading = readme.index("### 运行控制")
    results_heading = readme.index("### 筛选结果")
    education_heading = readme.index("### 学历核验")
    assert job_heading < run_heading < results_heading < education_heading
    assert "![岗位要求与筛选规则配置](docs/assets/user-guide/02-job-config-full.png)" in readme
    assert "![运行控制与筛选参数](docs/assets/user-guide/04-run-full.png)" in readme
    assert (DOCS_DIR / "assets" / "user-guide" / "02-job-config-full.png").is_file()
    assert (DOCS_DIR / "assets" / "user-guide" / "04-run-full.png").is_file()
