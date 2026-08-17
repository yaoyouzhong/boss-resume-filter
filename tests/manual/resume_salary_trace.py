"""人工追踪单份简历的薪资提取来源，不内置真实简历路径。"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from resume_parser import parse_resume_text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resume_file", type=Path, help="待追踪的本地简历文件")
    args = parser.parse_args()
    lines = parse_resume_text(args.resume_file).split("\n")

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("期望薪资："):
            print(i, "期望薪资分支:", repr(stripped[:100]))

    label_re = re.compile(
        r"(?:目前薪资|年收入|期望年薪|年薪|月薪|薪资要求|薪资待遇)"
        r"\s*[：:]\s*(.+)"
    )
    for i, line in enumerate(lines):
        match = label_re.search(line.strip())
        if match:
            print(
                i,
                "标签分支:",
                repr(line.strip()[:40]),
                "| val =",
                repr(match.group(1)[:60]),
            )

    first = lines[0].strip() if lines else ""
    print("首行长度:", len(first))
    if len(first) <= 40:
        print("走DOM兜底:", repr(first))


if __name__ == "__main__":
    main()
