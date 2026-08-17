"""批量解析目录内真实简历，检查解析与画像提取的覆盖情况。

人工测试：不纳入默认回归。逐份执行 parse_resume_text + extract_summary_info +
guess_name_from_filename，输出逐文件画像和可疑标记，便于发现未覆盖的格式。

用法：python tests/manual/resume_dir_parse_check.py <目录> [--report <报告路径>]
"""
import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bossmaster import extract_summary_info
from external_import_service import guess_name_from_filename
from resume_parser import parse_resume_text

EXTENSIONS = {".doc", ".docx", ".pdf"}


def _anomalies(path: Path, text: str, info: dict) -> list[str]:
    """标记提取结果中可疑的字段，供人工复核。"""
    flags: list[str] = []
    if len(text) < 100:
        flags.append("文本过短")
    salary = info.get("salary") or ""
    if salary:
        numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", salary)]
        if any(n >= 100 for n in numbers):
            flags.append(f"薪资可疑({salary})")
    for field, label in (
        ("age", "年龄"), ("education", "学历"), ("exp_years", "年限"),
        ("school", "学校"), ("company", "公司"), ("city", "城市"),
    ):
        if not (info.get(field) or ""):
            flags.append(f"无{label}")
    return flags


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="包含真实简历的本地目录")
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(tempfile.gettempdir()) / "resume_dir_parse_report.json",
        help="JSON 报告输出路径",
    )
    args = parser.parse_args()
    root = args.directory
    files = sorted(
        p for p in root.iterdir() if p.suffix.lower() in EXTENSIONS
    )
    print(f"目录: {root}  共 {len(files)} 个文件", flush=True)
    report = []
    for index, path in enumerate(files, 1):
        entry = {"file": path.name}
        try:
            text = parse_resume_text(str(path))
            info = extract_summary_info(text)
            entry.update(
                text_len=len(text),
                name_guess=guess_name_from_filename(path),
                salary=info.get("salary") or "",
                age=info.get("age") or "",
                gender=info.get("gender") or "",
                education=info.get("education") or "",
                exp_years=info.get("exp_years") or "",
                city=info.get("city") or "",
                school=info.get("school") or "",
                company=info.get("company") or "",
                job_status=info.get("job_status") or "",
                flags=_anomalies(path, text, info),
            )
        except Exception as exc:  # noqa: BLE001 诊断脚本需要汇总一切解析失败
            entry.update(error=f"{type(exc).__name__}: {exc}", flags=["解析失败"])
        report.append(entry)
        status = "/".join(entry.get("flags") or ["OK"])
        print(
            f"[{index:>2}/{len(files)}] {path.name} -> {status}",
            flush=True,
        )
    out = args.report
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    clean = sum(1 for e in report if not e.get("flags"))
    print(f"\n无标记 {clean}/{len(report)}，完整报告: {out}", flush=True)
    print("PARSE_DIR_CHECK_OK", flush=True)


if __name__ == "__main__":
    main()
