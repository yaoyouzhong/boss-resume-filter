"""人工薪资审计：逐文件展示提取值与来源上下文。"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bossmaster import extract_summary_info
from resume_parser import parse_resume_text

LABEL_RE = re.compile(r"[^\n]*(?:目前薪资|年收入|期望年薪|年薪|月薪|薪资要求|薪资待遇|期望薪资|目前收入)[^\n]*")
RANGE_RE = re.compile(r"[^\n]*\d+(?:\.\d+)?\s*[-~—]\s*\d+(?:\.\d+)?\s*[kK][^\n]*")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="包含简历的本地目录")
    root = parser.parse_args().directory
    files = sorted(
        path
        for path in root.iterdir()
        if path.suffix.lower() in (".doc", ".docx", ".pdf")
    )
    for p in files:
        text = parse_resume_text(str(p))
        salary = extract_summary_info(text)["salary"]
        ctx = ""
        for pat in (LABEL_RE, RANGE_RE):
            m = pat.search(text)
            if m:
                ctx = m.group(0).strip()[:70]
                break
        flag = ""
        if salary:
            nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", salary)]
            if any(n >= 50 for n in nums):
                flag += " [偏高]"
            if "." in salary:
                flag += " [折算]"
        elif re.search(r"薪|待遇", text):
            flag = " [有薪资词但未提取]"
        print(f"{p.name[:42]:<44} | {salary or '(空)':<12} | {ctx}{flag}")


if __name__ == "__main__":
    main()
