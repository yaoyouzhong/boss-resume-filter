"""解析覆盖诊断：逐文件打印可疑字段的上下文行。"""
import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from resume_parser import parse_resume_text

PATTERNS = {
    "薪资可疑": r"薪|元/月|万|收入",
    "无年龄": r"岁|出生|19[5-9]\d|20[0-2]\d",
    "无学历": r"学历|本科|专科|大专|硕士|博士|学士|MBA|mba|研究生",
    "无年限": r"年.{0,6}(经验|工作)|经验.{0,4}年|工作年限|从业",
    "无学校": r"大学|学院|学校|教育",
    "无公司": r"公司|集团|有限|工作(经验|经历|履历)|任职",
    "无城市": r"城市|地点|意向|南京|北京|上海|杭州|深圳|苏州|居住",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="生成报告时使用的简历目录")
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(tempfile.gettempdir()) / "resume_dir_parse_report.json",
        help="resume_dir_parse_check.py 生成的 JSON 报告",
    )
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    for entry in report:
        flags = [f for f in entry.get("flags", []) if f != "OK"]
        if not flags or "解析失败" in flags or "招聘需求" in entry["file"]:
            continue
        text = parse_resume_text(str(args.directory / entry["file"]))
        print("=" * 16, entry["file"], f"({len(text)}字)", "=" * 16)
        seen = set()
        for flag in flags:
            key = "薪资可疑" if flag.startswith("薪资可疑") else flag
            pattern = PATTERNS.get(key)
            if not pattern or key in seen:
                continue
            seen.add(key)
            hits = [
                line.strip() for line in text.split("\n") if re.search(pattern, line)
            ][:6]
            print(f"  --{key}--")
            for hit in hits:
                print("   ", hit[:110])


if __name__ == "__main__":
    main()
