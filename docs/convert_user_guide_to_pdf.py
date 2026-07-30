"""将图文版操作说明转换为适合打印和分发的 PDF。"""
from __future__ import annotations

import html
import re
import subprocess
import tempfile
from pathlib import Path
import sys

import markdown


DOCS_DIR = Path(__file__).resolve().parent
BASE_DIR = DOCS_DIR.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from subprocess_utils import hidden_subprocess  # noqa: E402

subprocess = hidden_subprocess(subprocess)

MD_PATH = DOCS_DIR / "BOSS招聘系统操作说明-图文版.md"
PDF_PATH = DOCS_DIR / "BOSS招聘系统操作说明-图文版.pdf"

CHROME_CANDIDATES = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
)

CSS = """
@page { size: A4; margin: 17mm 16mm 18mm; }
* { box-sizing: border-box; }
body {
  color: #172033;
  font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
  font-size: 10.5pt;
  line-height: 1.65;
  margin: 0;
}
h1 { color: #102a43; font-size: 22pt; margin: 0 0 10mm; text-align: center; }
h2 {
  border-bottom: 1px solid #cbd5e1;
  color: #155e9a;
  font-size: 16pt;
  margin: 9mm 0 4mm;
  padding-bottom: 2mm;
  page-break-after: avoid;
}
h3 { color: #334e68; font-size: 12.5pt; margin: 6mm 0 2mm; page-break-after: avoid; }
p { margin: 2.2mm 0; orphans: 3; widows: 3; }
ul, ol { margin: 2mm 0 3mm; padding-left: 7mm; }
li { margin: 1mm 0; }
table { border-collapse: collapse; font-size: 9.5pt; margin: 3mm 0 5mm; width: 100%; }
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
th, td { border: 1px solid #cbd5e1; padding: 1.8mm 2.2mm; text-align: left; vertical-align: top; }
th { background: #eaf3fb; color: #243b53; font-weight: 700; }
code { background: #eef2f7; border-radius: 2px; font-family: Consolas, monospace; padding: 0.2mm 1mm; }
pre { background: #f6f8fb; border: 1px solid #d9e2ec; border-radius: 4px; padding: 3mm; white-space: pre-wrap; }
img {
  border: 1px solid #d9e2ec;
  display: block;
  height: auto;
  margin: 4mm auto 6mm;
  max-height: 225mm;
  max-width: 100%;
  object-fit: contain;
  page-break-inside: avoid;
}
img[src*="10-today-tasks"], img[src*="12-contact-workbench"] { max-height: 100mm; }
blockquote { background: #eef7ff; border-left: 3px solid #2b8bd8; margin: 3mm 0; padding: 2mm 4mm; }
.process-flow {
  align-items: center;
  background: #f6f9fc;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  display: flex;
  flex-wrap: wrap;
  gap: 2mm;
  margin: 4mm 0 6mm;
  padding: 4mm;
  page-break-inside: avoid;
}
.flow-node { background: #fff; border: 1px solid #7cb6df; border-radius: 4px; padding: 1.5mm 2.5mm; }
.flow-arrow { color: #2b8bd8; font-weight: 700; }
"""


def _render_flow(source: str) -> str:
    """把文档中的 Mermaid 节点转换为无需联网的可打印流程条。"""
    labels: list[str] = []
    pattern = re.compile(
        r"\b[A-Z]\w*\s*(?:\[\s*[\"“]?(.*?)[\"”]?\s*\]|\{\s*[\"“]?(.*?)[\"”]?\s*\})"
    )
    for match in pattern.finditer(source):
        label = (match.group(1) or match.group(2) or "").strip().strip('"“”')
        if label and label not in labels:
            labels.append(label)
    if not labels:
        return ""
    items: list[str] = []
    for index, label in enumerate(labels):
        if index:
            items.append('<span class="flow-arrow">→</span>')
        items.append(f'<span class="flow-node">{html.escape(label)}</span>')
    return '<div class="process-flow">' + "".join(items) + "</div>"


def _build_html(markdown_text: str) -> str:
    markdown_text = re.sub(
        r"```mermaid\s*\n(.*?)```",
        lambda match: _render_flow(match.group(1)),
        markdown_text,
        flags=re.DOTALL,
    )
    body = markdown.markdown(
        markdown_text,
        extensions=("extra", "sane_lists"),
        output_format="html5",
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<base href="{DOCS_DIR.as_uri()}/">
<title>BOSS 招聘系统操作说明（图文版）</title>
<style>{CSS}</style>
</head>
<body>{body}</body>
</html>"""


def main() -> None:
    chrome = next((path for path in CHROME_CANDIDATES if path.exists()), None)
    if chrome is None:
        raise FileNotFoundError("未找到 Chrome 或 Edge，无法生成 PDF")

    source = MD_PATH.read_text(encoding="utf-8")
    html_text = _build_html(source)
    with tempfile.TemporaryDirectory(prefix="boss_user_guide_pdf_") as tmp_dir:
        html_path = Path(tmp_dir) / "user-guide.html"
        html_path.write_text(html_text, encoding="utf-8")
        result = subprocess.run(
            [
                str(chrome),
                "--headless",
                "--disable-gpu",
                "--no-sandbox",
                f"--print-to-pdf={PDF_PATH}",
                "--print-to-pdf-no-header",
                "--no-pdf-header-footer",
                html_path.as_uri(),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
    if result.returncode != 0 or not PDF_PATH.exists():
        raise RuntimeError(result.stderr.strip() or "PDF 未生成")
    print(f"PDF 已生成：{PDF_PATH} ({PDF_PATH.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
