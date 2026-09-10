"""Conservative field extraction from explicitly labelled resume tables."""
from __future__ import annotations

import re

_DATE_PREFIX = re.compile(r"^([0-9\s./年月日—–－\-~～至今]+)(.*)$")
_PERIOD = re.compile(
    r"((?:19|20)\d{2})[/.年](1[0-2]|0?[1-9])月?[-—–－~～至]*"
    r"(?:(至今)|((?:19|20)\d{2})[/.年](1[0-2]|0?[1-9])月?)"
)
_STOP = re.compile(r"项目(?:开发|研发|测试)?(?:经历|经验)|项目\s*[0-9一二三四五六七八九十]+[：:]|教育经历|自我评价")
_DESCRIPTION = re.compile(r"(?:^|\s)(?:\d+[.)）、]|负责|主要|参与|从事|外派|在职|工作职责)")
_BAD_NAME = re.compile(r"起止|单位名称|工作|经历|职责|项目|描述|负责|参与|从事|采集|开发|搭建|需求|测试|维护|系统|包括|进行|实现|通过|以及|编写|分享")
_SUFFIX = re.compile(r"(?:有限责任公司|股份有限公司|有限公司|集团|公司)$")
_CONTINUATION = re.compile(r"^(?:有限|股份|责任|公司|科技|技术|信息|软件|服务|信软件|份有限|术有限公司|限公司|司$)")


def _name_fragment(line: str) -> str:
    """Accept only the leading name cell, never a company found in prose."""
    value = _DESCRIPTION.split(line, maxsplit=1)[0].strip()
    value = re.sub(r"(?<=[一-龥])\s+(?=[一-龥])", "", value)
    if (not 2 <= len(value) <= 36 or _BAD_NAME.search(value)
            or not re.fullmatch(r"[一-龥A-Za-z（）()·& ]+", value)
            or value in {"至今", "单位", "起止时间", "公司", "有限公司"}):
        return ""
    return value


def _complete_name(value: str) -> bool:
    """Do not complete a clipped legal suffix using outside knowledge."""
    return bool(value) and not re.search(r"(?:有限公|有限|股份有|股份|科技股|信息技|软件有)$", value)


def company_from_work_table(text: str) -> str | None:
    """Return latest table employer, empty when ambiguous, or None if no table.

    PDF readers may emit rows or groups of columns. A labelled time/name table
    can be paired by column order only when complete period/name counts agree.
    The table's description column is never searched for employer names.
    """
    start = re.search(r"(?:^|\n)\s*工作(?:经验|经历)\s*\n", text)
    if not start:
        return None
    body = text[start.end():]
    header = re.search(r"起止时间\s*单位名称\s*工作经历简述", body[:160])
    if not header:
        return None
    body = body[header.end():]
    stop = _STOP.search(body)
    if stop:
        body = body[:stop.start()]
    date_parts: list[str] = []
    names: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.fullmatch(r"\d+[.)）、]", line):
            continue
        date = _DATE_PREFIX.match(line)
        if date:
            prefix, line = date.groups()
            # Only time-column fragments; numbered responsibilities end in ')',
            # '、', or include prose and are rejected below.
            if line and not re.search(r"(?:19|20)\d{2}|至今", prefix):
                continue
            date_parts.append(re.sub(r"\s", "", prefix))
            line = line.strip()
        if not line:
            continue
        # Some readers insert blanks and even other columns between a wrapped
        # name and its suffix. A bounded suffix continuation may extend only
        # an unfinished preceding name; it cannot consume a second full name.
        compact = re.sub(r"\s", "", line)
        if (names and not _SUFFIX.search(names[-1])
                and _CONTINUATION.match(compact)
                and re.fullmatch(r"[一-龥]{1,16}", compact)
                and not _BAD_NAME.search(compact)):
            names[-1] += compact
            continue
        fragment = _name_fragment(line)
        if fragment:
            names.append(fragment)
    dates = "".join(date_parts)
    periods = list(_PERIOD.finditer(dates))
    if not periods or len(periods) != len(names):
        return ""
    # Reject partially parsed dates instead of silently accepting e.g. month 13
    # as month 1. Every time-column character must belong to a complete period.
    cursor = 0
    for period in periods:
        if period.start() != cursor:
            return ""
        cursor = period.end()
    if cursor != len(dates):
        return ""
    candidates: list[tuple[tuple[int, int, int, int], str]] = []
    for period, name in zip(periods, names):
        sy, sm, present, ey, em = period.groups()
        start_date = int(sy), int(sm)
        end_date = (9999, 12) if present else (int(ey), int(em))
        if not 1 <= start_date[1] <= 12 or not 1 <= end_date[1] <= 12 or end_date < start_date:
            return ""
        candidates.append(((*end_date, *start_date), name))
    latest = max(key for key, _ in candidates)
    latest_names = {name for key, name in candidates if key == latest}
    if len(latest_names) != 1:
        return ""
    name = latest_names.pop()
    return name if _complete_name(name) else ""
