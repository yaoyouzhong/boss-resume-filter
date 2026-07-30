"""将 education-tool-manual.md 转为 PDF（Chrome 无头打印，零额外依赖）。"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from subprocess_utils import hidden_subprocess  # noqa: E402

subprocess = hidden_subprocess(subprocess)

MD_PATH = SCRIPT_DIR / "education-tool-manual.md"
PDF_PATH = SCRIPT_DIR / "EducationCertificateTool-Manual.pdf"

# ---------- Markdown → 简易 HTML ----------

_CSS = """
@page {
    size: A4;
    margin: 20mm 18mm 20mm 18mm;
}
body {
    font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
    font-size: 11pt;
    line-height: 1.7;
    color: #1a1a1a;
    max-width: 100%;
}
h1 {
    font-size: 20pt;
    text-align: center;
    border-bottom: 2px solid #2563eb;
    padding-bottom: 10px;
    margin-bottom: 24px;
}
h2 {
    font-size: 15pt;
    color: #1e40af;
    border-bottom: 1px solid #cbd5e1;
    padding-bottom: 4px;
    margin-top: 28px;
    page-break-after: avoid;
}
h3 {
    font-size: 12.5pt;
    color: #334155;
    margin-top: 20px;
    page-break-after: avoid;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 12px 0;
    font-size: 10.5pt;
    page-break-inside: avoid;
}
th, td {
    border: 1px solid #d1d5db;
    padding: 6px 10px;
    text-align: left;
}
th {
    background-color: #f1f5f9;
    font-weight: 600;
}
tr:nth-child(even) {
    background-color: #f8fafc;
}
code {
    font-family: "Cascadia Code", "Consolas", "Source Code Pro", monospace;
    background-color: #f1f5f9;
    padding: 1px 4px;
    border-radius: 3px;
    font-size: 10pt;
}
pre {
    background-color: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 12px 16px;
    font-size: 9.5pt;
    line-height: 1.5;
    overflow-x: auto;
    white-space: pre-wrap;
    word-wrap: break-word;
    page-break-inside: avoid;
}
pre code {
    background: none;
    padding: 0;
}
blockquote {
    border-left: 3px solid #2563eb;
    margin: 12px 0;
    padding: 6px 16px;
    background-color: #eff6ff;
    color: #1e40af;
    font-size: 10.5pt;
}
hr {
    border: none;
    border-top: 1px solid #e2e8f0;
    margin: 20px 0;
}
ul, ol {
    padding-left: 24px;
}
li {
    margin-bottom: 4px;
}
strong {
    color: #0f172a;
}
"""


def _md_to_html(md_text: str) -> str:
    """极简 Markdown → HTML 转换（支持标题、表格、代码块、列表、粗体、行内代码、引用、分隔线）。"""
    lines = md_text.split("\n")
    html_parts: list[str] = []
    in_code = False
    in_table = False
    in_ul = False
    in_ol = False
    in_blockquote = False

    def close_lists():
        nonlocal in_ul, in_ol
        parts = []
        if in_ul:
            parts.append("</ul>")
            in_ul = False
        if in_ol:
            parts.append("</ol>")
            in_ol = False
        return parts

    def close_blockquote():
        nonlocal in_blockquote
        if in_blockquote:
            in_blockquote = False
            return ["</blockquote>"]
        return []

    def close_table():
        nonlocal in_table
        if in_table:
            in_table = False
            return ["</tbody></table>"]
        return []

    def inline(text: str) -> str:
        # 行内代码
        text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
        # 粗体
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        # 斜体
        text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
        # 链接
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
        return text

    i = 0
    while i < len(lines):
        line = lines[i]

        # 代码块
        if line.strip().startswith("```"):
            if in_code:
                html_parts.append("</code></pre>")
                in_code = False
            else:
                html_parts.extend(close_lists())
                html_parts.extend(close_blockquote())
                html_parts.extend(close_table())
                lang = line.strip()[3:].strip()
                html_parts.append(f'<pre><code class="language-{lang}">' if lang else "<pre><code>")
                in_code = True
            i += 1
            continue

        if in_code:
            html_parts.append(
                line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
            i += 1
            continue

        stripped = line.strip()

        # 空行
        if not stripped:
            html_parts.extend(close_lists())
            html_parts.extend(close_blockquote())
            html_parts.extend(close_table())
            i += 1
            continue

        # 分隔线
        if re.match(r"^-{3,}$", stripped) or re.match(r"^\*{3,}$", stripped):
            html_parts.extend(close_lists())
            html_parts.extend(close_blockquote())
            html_parts.extend(close_table())
            html_parts.append("<hr>")
            i += 1
            continue

        # 标题
        hm = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if hm:
            html_parts.extend(close_lists())
            html_parts.extend(close_blockquote())
            html_parts.extend(close_table())
            level = len(hm.group(1))
            html_parts.append(f"<h{level}>{inline(hm.group(2))}</h{level}>")
            i += 1
            continue

        # 表格
        if "|" in stripped and stripped.startswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            # 分隔行
            if all(re.match(r"^[-:]+$", c) for c in cells):
                i += 1
                continue
            if not in_table:
                html_parts.extend(close_lists())
                html_parts.extend(close_blockquote())
                html_parts.append('<table><thead><tr>')
                for c in cells:
                    html_parts.append(f"<th>{inline(c)}</th>")
                html_parts.append("</tr></thead><tbody>")
                in_table = True
            else:
                html_parts.append("<tr>")
                for c in cells:
                    html_parts.append(f"<td>{inline(c)}</td>")
                html_parts.append("</tr>")
            i += 1
            continue

        # 引用
        if stripped.startswith(">"):
            text = stripped[1:].strip()
            if not in_blockquote:
                html_parts.extend(close_lists())
                html_parts.extend(close_table())
                html_parts.append("<blockquote>")
                in_blockquote = True
            html_parts.append(f"<p>{inline(text)}</p>")
            i += 1
            continue

        # 无序列表
        um = re.match(r"^[-*]\s+(.+)$", stripped)
        if um:
            html_parts.extend(close_blockquote())
            html_parts.extend(close_table())
            if not in_ul:
                html_parts.extend(close_lists())
                html_parts.append("<ul>")
                in_ul = True
            html_parts.append(f"<li>{inline(um.group(1))}</li>")
            i += 1
            continue

        # 有序列表
        om = re.match(r"^\d+\.\s+(.+)$", stripped)
        if om:
            html_parts.extend(close_blockquote())
            html_parts.extend(close_table())
            if not in_ol:
                html_parts.extend(close_lists())
                html_parts.append("<ol>")
                in_ol = True
            html_parts.append(f"<li>{inline(om.group(1))}</li>")
            i += 1
            continue

        # 普通段落
        html_parts.extend(close_lists())
        html_parts.extend(close_blockquote())
        html_parts.extend(close_table())
        html_parts.append(f"<p>{inline(stripped)}</p>")
        i += 1

    html_parts.extend(close_lists())
    html_parts.extend(close_blockquote())
    html_parts.extend(close_table())
    if in_code:
        html_parts.append("</code></pre>")

    body = "\n".join(html_parts)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>{_CSS}</style>
</head>
<body>
{body}
</body>
</html>"""


def main() -> None:
    md_text = MD_PATH.read_text(encoding="utf-8")
    html = _md_to_html(md_text)

    tmp_html = Path(tempfile.mktemp(suffix=".html"))
    try:
        tmp_html.write_text(html, encoding="utf-8")

        cmd = [
            CHROME,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--print-to-pdf=" + str(PDF_PATH),
            "--print-to-pdf-no-header",
            "--no-pdf-header-footer",
            str(tmp_html.as_uri()),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"Chrome 错误：{result.stderr}", file=sys.stderr)
            sys.exit(1)

        if not PDF_PATH.exists():
            print("PDF 未生成", file=sys.stderr)
            sys.exit(1)

        size_kb = PDF_PATH.stat().st_size / 1024
        print(f"PDF 已生成：{PDF_PATH}  ({size_kb:.0f} KB)")
    finally:
        tmp_html.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
