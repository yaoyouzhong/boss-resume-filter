"""legacy_doc_converter 单元测试：COM 调用全程打桩，不启动真实 Word。"""
import base64
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import legacy_doc_converter
from legacy_doc_converter import (
    LegacyDocConversionError,
    _powershell_literal,
    _summarize_failure,
    _unwrap_clixml,
    convert_legacy_doc,
)


def _capture(callback):
    try:
        callback()
    except LegacyDocConversionError as exc:
        return exc
    raise AssertionError("应当抛出 LegacyDocConversionError")


def _decode_script(command: list[str]) -> str:
    """从 EncodedCommand 形式的调用中还原 PowerShell 脚本。"""
    encoded = command[command.index("-EncodedCommand") + 1]
    return base64.b64decode(encoded).decode("utf-16-le")


def _extract_target(script: str) -> Path:
    targets = re.findall(
        r"WriteAllText\(\s*'((?:[^']|'')+)'",
        script,
        re.S,
    )
    text_target = next(target for target in targets if target.lower().endswith(".txt"))
    return Path(text_target.replace("''", "'"))


def test_powershell_literal_quotes_chinese_spaces_and_single_quotes():
    assert _powershell_literal(r"D:\简历\张三.doc") == r"'D:\简历\张三.doc'"
    assert _powershell_literal("it's.doc") == "'it''s.doc'"


def test_script_opens_word_invisible_without_macros():
    script = legacy_doc_converter._LEGACY_DOC_PS_SCRIPT
    assert "$word.Visible = $false" in script
    assert "$word.DisplayAlerts = 0" in script
    assert "$word.AutomationSecurity = 3" in script  # 强制禁用宏
    assert "$word.Quit()" in script  # finally 退出本进程创建的 Word
    assert "GetWindowThreadProcessId" in script
    assert "__PID_TARGET__" in script


def test_timeout_cleanup_terminates_only_tracked_winword_process():
    with tempfile.TemporaryDirectory() as tmp:
        pid_target = Path(tmp) / "word.pid"
        pid_target.write_text("4321", encoding="ascii")
        responses = [
            SimpleNamespace(
                returncode=0,
                stdout=b'"WINWORD.EXE","4321","Console","1","10,000 K"',
                stderr=b"",
            ),
            SimpleNamespace(returncode=0, stdout=b"SUCCESS", stderr=b""),
        ]
        calls = []

        def fake_run(command, **_kwargs):
            calls.append(command)
            return responses.pop(0)

        with patch.object(legacy_doc_converter.subprocess, "run", fake_run):
            assert legacy_doc_converter._terminate_tracked_word(pid_target) is True
        assert calls[0][0] == "tasklist.exe"
        assert calls[1] == ["taskkill.exe", "/PID", "4321", "/T", "/F"]


def test_script_reads_all_story_ranges_via_protected_view():
    """统一经受保护视图打开（等价于直接打开且不被策略拒绝），遍历全部故事层。"""
    script = legacy_doc_converter._LEGACY_DOC_PS_SCRIPT
    assert "$word.ProtectedViewWindows.Open(__SOURCE__)" in script
    assert "Documents.Open" not in script  # 不再做必败的直接打开尝试
    assert "$protectedView.Document.StoryRanges" in script  # 简历正文常在文本框故事层
    assert "$current.NextStoryRange" in script
    assert "UTF8Encoding($false)" in script  # 无 BOM UTF-8 写文本产物
    assert "$protectedView.Close()" in script


def test_non_windows_platform_is_rejected_before_spawning():
    with patch.object(sys, "platform", "darwin"):
        exc = _capture(lambda: convert_legacy_doc("resume.doc"))
    assert "当前系统不支持自动转换" in exc.reason


def test_missing_word_installation_is_classified_from_com_error():
    def fake_run(command, **_kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=b"",
            stderr="New-Object : 检索 COM 类工厂失败 0x80040154".encode("gbk"),
        )

    with patch.object(legacy_doc_converter.subprocess, "run", fake_run):
        exc = _capture(lambda: convert_legacy_doc("resume.doc"))
    assert exc.reason == "未检测到本机安装的 Microsoft Word"


def test_timeout_and_empty_output_are_classified_and_cleaned_up():
    captured = {}

    def fake_timeout(command, **_kwargs):
        captured["script"] = _decode_script(command)
        raise subprocess.TimeoutExpired(command, 60)

    with patch.object(legacy_doc_converter.subprocess, "run", fake_timeout):
        exc = _capture(lambda: convert_legacy_doc("张三 简历.doc"))
    assert "转换超时" in exc.reason
    # 失败路径必须回收临时目录（脚本里的中文与空格路径经字面量转义）
    temp_dir = _extract_target(captured["script"]).parent
    assert not temp_dir.exists()
    assert "张三 简历.doc'" in captured["script"]  # 带空格中文名被完整包进字面量

    def fake_empty(command, **_kwargs):
        captured["script"] = _decode_script(command)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    with patch.object(legacy_doc_converter.subprocess, "run", fake_empty):
        exc = _capture(lambda: convert_legacy_doc("resume.doc"))
    assert "未生成有效的转换文件" in exc.reason
    assert not _extract_target(captured["script"]).parent.exists()


def test_success_returns_generated_text_inside_fresh_temp_dir():
    def fake_run(command, **_kwargs):
        script = _decode_script(command)
        target = _extract_target(script)
        target.write_text("李四 简历全文", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    with patch.object(legacy_doc_converter.subprocess, "run", fake_run):
        converted = convert_legacy_doc("李四-简历.doc")
    try:
        assert converted.exists()
        assert converted.suffix == ".txt"
        assert converted.stem == "李四-简历"
        assert converted.read_text(encoding="utf-8") == "李四 简历全文"
        assert converted.parent.name.startswith("boss-doc-convert-")
    finally:
        import shutil

        shutil.rmtree(converted.parent, ignore_errors=True)


def test_summarize_failure_keeps_first_meaningful_line():
    detail = "\nSaveAs2 : 文件已损坏无法打开\nAt line:9 char:5\n+ ...\n"
    assert _summarize_failure(detail) == "SaveAs2 : 文件已损坏无法打开"
    assert _summarize_failure("") == "本机 Word 转换未返回结果"


def test_summarize_failure_maps_trust_center_block():
    assert "信任中心" in _summarize_failure(
        "0x800A18A0 试图打开的文件类型已被信任中心的文件阻止设置阻止"
    )


def test_unwrap_clixml_extracts_real_error_lines():
    # 真实捕获：PowerShell 重定向错误流时输出的 CLIXML 序列化片段
    clixml = (
        '#< CLIXML\n<Objs Version="1.1.0.1" '
        'xmlns="http://schemas.microsoft.com/powershell/2004/04">'
        '<Obj S="progress" RefId="0"></Obj>'
        '<S S="Error">试图打开的文件类型被阻止_x000D__x000A_</S>'
        '<S S="Error">At line:5 char:9_x000D__x000A_</S></Objs>'
    )
    text = _unwrap_clixml(clixml)
    assert "试图打开的文件类型被阻止" in text
    assert "_x000D_" not in text


def test_unwrap_clixml_keeps_plain_console_lines_before_xml_block():
    """真实捕获：脚本 [Console]::Error 直写的 HRESULT 行在 <Objs> 块之前。"""
    mixed = (
        "#< CLIXML\r\n"
        "0x800A18A0 试图打开的文件类型已被信任中心的文件阻止设置阻止。\r\n"
        '<Objs Version="1.1.0.1"><Obj S="progress" RefId="0"></Obj></Objs>'
    )
    text = _unwrap_clixml(mixed)
    assert text.splitlines()[0].startswith("0x800A18A0")
    assert "信任中心" in _summarize_failure(text)
