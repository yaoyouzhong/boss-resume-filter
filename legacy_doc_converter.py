"""旧版 .doc 简历的本机 Word 全文提取。

通过系统自带的 powershell.exe 驱动本机安装的 Microsoft Word COM，
经受保护视图（Protected View）隐身打开文档——受保护视图只读不存，
是信任中心策略始终允许的阅读方式，且与直接打开的提取结果完全等价；
随后遍历全部故事层（StoryRanges，含文本框）提取全文，写成临时
UTF-8 文本。全程仅在本机完成，不访问网络；所有失败统一抛出
LegacyDocConversionError，由调用方决定如何向用户解释。
"""
from __future__ import annotations

import base64
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from subprocess_utils import hidden_subprocess

subprocess = hidden_subprocess(subprocess)


class LegacyDocConversionError(RuntimeError):
    """旧版 .doc 无法在本机 Word 中提取全文。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"旧版 .doc 转换失败：{reason}")


DOC_CONVERSION_TIMEOUT_SECONDS = 90  # 含大量嵌入对象的老 .doc 实测可达 50 秒

# 隐身打开、禁用宏（msoAutomationSecurityForceDisable=3）。统一经受保护视图
# 打开：与直接打开提取结果等价，且不会被信任中心"文件阻止"策略拒绝（省去
# 一次必败的直接打开尝试）。遍历 StoryRanges（含文本框，简历模板常把正文
# 放进文本框，主文本层可能几乎为空）拼接全文；错误经 [Console]::Error 以
# 纯文本输出（避免 PowerShell 把错误流序列化成 CLIXML）；finally 保证退出
# 本进程创建的 Word。
_LEGACY_DOC_PS_SCRIPT = """
$ErrorActionPreference = 'Stop'
$word = $null
$exitCode = 0
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    try {
        if (-not ("BossWordPidProbe" -as [type])) {
            Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class BossWordPidProbe {
    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
}
"@
        }
        [uint32]$wordPid = 0
        [void][BossWordPidProbe]::GetWindowThreadProcessId([IntPtr]$word.Hwnd, [ref]$wordPid)
        if ($wordPid -gt 0) {
            [System.IO.File]::WriteAllText(__PID_TARGET__, $wordPid.ToString())
        }
    } catch { }
    $protectedView = $word.ProtectedViewWindows.Open(__SOURCE__)
    try {
        $builder = New-Object System.Text.StringBuilder
        foreach ($story in $protectedView.Document.StoryRanges) {
            $current = $story
            while ($current -ne $null) {
                [void]$builder.Append($current.Text)
                $current = $current.NextStoryRange
            }
        }
        [System.IO.File]::WriteAllText(
            __TEXT_TARGET__, $builder.ToString(), (New-Object System.Text.UTF8Encoding($false))
        )
    } finally {
        try { $protectedView.Close() } catch { }
    }
} catch {
    [Console]::Error.WriteLine(("0x" + ("{0:X8}" -f $_.Exception.HResult)) + " " + $_.Exception.Message)
    $exitCode = 1
} finally {
    if ($word -ne $null) {
        try { $word.Quit() } catch { }
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($word)
    }
}
exit $exitCode
"""

# COM HRESULT → 用户可读原因；未识别的回退到错误首行。
_KNOWN_FAILURE_REASONS: tuple[tuple[str, str], ...] = (
    ("0x80040154", "未检测到本机安装的 Microsoft Word"),
    ("0x80080005", "本机 Word 启动失败"),
    ("0x800A18A0", "本机 Word 的安全设置（信任中心）阻止打开旧版 .doc 文件"),
)


def convert_legacy_doc(source_path: str | Path) -> Path:
    """Extract the full text of one legacy .doc file via local Word.

    Returns the temporary UTF-8 .txt path; the caller owns cleanup of its
    parent directory. Raises LegacyDocConversionError for every expected
    failure (no Word, COM failure, timeout, empty output).
    """
    source = Path(source_path)
    if sys.platform != "win32":
        raise LegacyDocConversionError("当前系统不支持自动转换")

    temp_dir = Path(tempfile.mkdtemp(prefix="boss-doc-convert-"))
    try:
        text_target = temp_dir / f"{source.stem}.txt"
        pid_target = temp_dir / "word.pid"
        script = _LEGACY_DOC_PS_SCRIPT.replace(
            "__SOURCE__", _powershell_literal(str(source.resolve()))
        ).replace("__TEXT_TARGET__", _powershell_literal(str(text_target))).replace(
            "__PID_TARGET__", _powershell_literal(str(pid_target))
        )
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        try:
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-EncodedCommand",
                    encoded,
                ],
                capture_output=True,
                timeout=DOC_CONVERSION_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            raise LegacyDocConversionError("未找到 PowerShell，无法调用本机 Word") from exc
        except subprocess.TimeoutExpired as exc:
            _terminate_tracked_word(pid_target)
            raise LegacyDocConversionError("调用本机 Word 转换超时") from exc
        except OSError as exc:
            raise LegacyDocConversionError(str(exc)) from exc

        if completed.returncode != 0:
            detail = _decode_process_output(completed.stderr) or _decode_process_output(
                completed.stdout
            )
            raise LegacyDocConversionError(_summarize_failure(detail))
        if not text_target.exists() or text_target.stat().st_size == 0:
            raise LegacyDocConversionError("本机 Word 未生成有效的转换文件")
        return text_target
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _terminate_tracked_word(pid_target: Path) -> bool:
    """Terminate only the WINWORD process created by this conversion after timeout."""
    try:
        pid = int(pid_target.read_text(encoding="ascii").strip())
    except (OSError, TypeError, ValueError):
        return False
    try:
        check = subprocess.run(
            ["tasklist.exe", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            timeout=10,
        )
        process_list = _decode_process_output(check.stdout).upper()
        if check.returncode != 0 or "WINWORD.EXE" not in process_list:
            return False
        killed = subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            timeout=10,
        )
        return killed.returncode == 0
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False


def _powershell_literal(text: str) -> str:
    """Quote a string as a PowerShell single-quoted literal."""
    return "'" + text.replace("'", "''") + "'"


def _decode_process_output(raw: bytes | None) -> str:
    """Decode PowerShell output across common Windows encodings."""
    if not raw:
        return ""
    for encoding in ("utf-8", "gbk"):
        try:
            text = raw.decode(encoding).strip()
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace").strip()
    if text.startswith("#< CLIXML"):
        text = _unwrap_clixml(text)
    return text


def _unwrap_clixml(text: str) -> str:
    """Unwrap PowerShell's CLIXML stream: plain console lines plus error records.

    脚本里的 [Console]::Error 直写是纯文本，宿主自己的错误/进度记录则序列化
    在 <Objs> 包装里；两者可能同时出现（先去掉 XML 块、取纯文本行，再附加
    <S S="Error"> 记录）。
    """
    extracted = [
        re.sub(r"_x[0-9A-Fa-f]{4}_", " ", match).strip()
        for match in re.findall(r'<S S="Error">(.*?)</S>', text, flags=re.S)
    ]
    plain = re.sub(r"<Objs\b.*</Objs>", "", text, flags=re.S)
    plain = plain.replace("#< CLIXML", "").strip()
    lines = [line.strip() for line in plain.splitlines() if line.strip()]
    lines.extend(line for line in extracted if line)
    return "\n".join(lines)


def _summarize_failure(detail: str) -> str:
    """Map a raw PowerShell/COM failure to a short user-facing reason."""
    for marker, reason in _KNOWN_FAILURE_REASONS:
        if marker in detail:
            return reason
    if "REGDB" in detail:
        return "未检测到本机安装的 Microsoft Word"
    first_line = next(
        (line.strip() for line in detail.splitlines() if line.strip()),
        "",
    )
    return first_line[:120] or "本机 Word 转换未返回结果"
