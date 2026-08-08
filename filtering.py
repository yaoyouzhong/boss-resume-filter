"""Pure candidate filtering rules for BOSS resume screening."""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

from constants import (
    MAJOR_CITIES,
    SCORE_BASE,
    SCORE_SKILL_MAX,
    SCORE_EXP_MAX,
    SCORE_EXP_MULTIPLIER,
    SCORE_EDU_DOCTOR,
    SCORE_EDU_MASTER_985,
    SCORE_EDU_MASTER,
    SCORE_EDU_BACHELOR_985,
    SCORE_EDU_BACHELOR,
    SCORE_THRESHOLD_PASS,
    SCORE_THRESHOLD_RECOMMEND,
    SCORE_THRESHOLD_STRONG,
    CHINESE_NUMERALS,
    NON_REGULAR_EDU,
)


_major_cities_set = set(MAJOR_CITIES)
_PREFERRED_BONUS_MAX = 10
QUALIFIED = "qualified"
REJECTED = "rejected"
MANUAL_REVIEW = "manual_review"
GENDER_ANY = "不限"
GENDER_MALE = "男"
GENDER_FEMALE = "女"
GENDER_VALUES = (GENDER_ANY, GENDER_MALE, GENDER_FEMALE)
_EXPLICIT_NON_REGULAR_EDU = (
    "自考", "成教", "函授", "夜大", "网络教育", "继续教育", "非统招",
    "专升本", "电大", "远程教育", "成人高考", "成人教育", "业余",
)


def normalize_gender(value: Any) -> str | None:
    """Normalize an explicit textual gender value used by job rules."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    text = re.sub(r'^性别\s*[：:]\s*', '', text).strip()
    compact = re.sub(r'[\s_\-]+', '', text)
    if compact in {"男", "男性", "male", "man", "m"}:
        return GENDER_MALE
    if compact in {"女", "女性", "female", "woman", "f"}:
        return GENDER_FEMALE
    return None


def normalize_candidate_gender(value: Any) -> str | None:
    """Normalize a BOSS candidate gender value, including ``geekGender`` codes.

    The current BOSS recruiter web client renders ``geekGender == 1`` with the
    male icon and ``geekGender == 0`` with the female icon.  Numeric mapping is
    intentionally isolated from :func:`normalize_gender`, because ``0`` means
    "不限" in some job-search request parameters and must not be accepted as a
    job-rule value.
    """
    normalized = normalize_gender(value)
    if normalized:
        return normalized
    text = str(value if value is not None else "").strip()
    text = re.sub(r'^性别\s*[：:]\s*', '', text).strip()
    if text == "1":
        return GENDER_MALE
    if text == "0":
        return GENDER_FEMALE
    return None


_CERT_ALIASES = {
    '证券从业资格': ['证券从业', '证券行业', '证券相关', '证券背景', '证券经验'],
    '基金从业资格': ['基金从业', '基金行业', '基金相关', '基金背景'],
    '期货从业资格': ['期货从业', '期货行业', '期货相关'],
    '银行从业资格': ['银行从业', '银行业', '银行相关'],
    'CFA': ['cfa', 'CFA', '特许金融分析师'],
    'CPA': ['cpa', 'CPA', '注册会计师'],
    'FRM': ['frm', 'FRM', '金融风险管理师'],
}

_INDUSTRY_ALIASES = {
    '债券': ['固收', '固定收益', '利率债', '信用债', '国债', '公司债', '企业债', '可转债'],
    '基金': ['公募', '私募', 'ETF', 'FOF', '货币基金', '债券基金', '股票基金'],
    '期货': ['商品期货', '金融期货', '股指期货', 'CTA'],
    '期权': ['衍生品', '结构化产品', '场外期权', '场内期权'],
    '量化': ['量化交易', '量化策略', '量化模型', '因子模型', 'alpha策略'],
    '证券': ['券商', '证券公司', '证券交易'],
}

_EDU_ALIASES = {
    '985 院校': ['985', '985院校', '985高校', '985大学'],
    '211 院校': ['211', '211院校', '211高校', '211大学'],
    '双一流院校': ['双一流', '双一流院校', '双一流高校', '双一流大学'],
    '全日制本科': ['全日制本科', '统招本科'],
}

_SKILL_ALIASES = {
    'AI Agent': ['Agent', 'AIAgent', '智能体', '大模型Agent', '大模型 Agent'],
}


def _text_snippet(text: str, start: int, end: int, radius: int = 18) -> str:
    """Return a compact evidence snippet around a matched span."""
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    snippet = text[left:right].replace('\n', ' ').strip()
    if left > 0:
        snippet = '...' + snippet
    if right < len(text):
        snippet += '...'
    return snippet


def _find_item_evidence(candidate_text: str, item: str) -> str:
    """Find a short candidate-text snippet for a keyword or aliased condition."""
    special_exp = re.match(r'(.+?)经验≥(\d+)年$', item)
    if special_exp:
        item = special_exp.group(1).strip()

    terms = [item]
    terms.extend(_CERT_ALIASES.get(item, []))
    terms.extend(_INDUSTRY_ALIASES.get(item, []))
    terms.extend(_EDU_ALIASES.get(item, []))
    terms.extend(_SKILL_ALIASES.get(item, []))

    for term in terms:
        if not term:
            continue
        if any('一' <= c <= '鿿' for c in term):
            idx = candidate_text.lower().find(term.lower())
            if idx >= 0:
                return _text_snippet(candidate_text, idx, idx + len(term))
            continue
        try:
            match = re.search(r'\b' + re.escape(term) + r'\b', candidate_text, re.IGNORECASE)
        except re.error:
            match = None
        if match:
            return _text_snippet(candidate_text, match.start(), match.end())
        idx = candidate_text.lower().find(term.lower())
        if idx >= 0:
            return _text_snippet(candidate_text, idx, idx + len(term))
    return ""


def _condition_item_found(candidate_text: str, item: str) -> bool:
    """Match a required/preferred item with known aliases."""
    special_exp = re.match(r'(.+?)经验≥(\d+)年$', item)
    if special_exp:
        domain = special_exp.group(1).strip()
        required_years = int(special_exp.group(2))
        return _condition_item_found(candidate_text, domain) and (parse_experience_years(candidate_text) or 0) >= required_years

    if item.lower() in candidate_text.lower():
        return True
    aliases = (
        _CERT_ALIASES.get(item, [])
        + _INDUSTRY_ALIASES.get(item, [])
        + _EDU_ALIASES.get(item, [])
        + _SKILL_ALIASES.get(item, [])
    )
    return any(alias.lower() in candidate_text.lower() for alias in aliases)


def _extract_city(text: str) -> str:
    """从候选人摘要中提取期望城市名"""
    if not text:
        return ""
    city_match = re.search(r'(?:意向|城市|地点)[：:\s]*([一-龥]{2,4})', text)
    if city_match:
        raw = city_match.group(1)
        m = re.match(r'([一-龥]{2,3})市', raw)
        if m and m.group(1) in _major_cities_set:
            return m.group(1)
        if raw in _major_cities_set:
            return raw
    for city in MAJOR_CITIES:
        if city in text:
            return city
    return ""


def parse_experience_years(text: str) -> Optional[int]:
    """从文本中解析工作年限，支持阿拉伯数字和中文数字。"""

    if not text:
        return None

    def parse_chinese_num(chinese_num: str) -> Optional[int]:
        if chinese_num == '十':
            return 10
        if chinese_num.startswith('十') and len(chinese_num) > 1:
            return 10 + CHINESE_NUMERALS.get(chinese_num[1], 0)
        if chinese_num.endswith('十') and len(chinese_num) > 1:
            return CHINESE_NUMERALS.get(chinese_num[0], 0) * 10
        if len(chinese_num) == 2:
            first = CHINESE_NUMERALS.get(chinese_num[0], 0)
            second = CHINESE_NUMERALS.get(chinese_num[1], 0)
            if 2 <= first <= 9:
                return first * 10 + second
            return first + second
        if chinese_num in CHINESE_NUMERALS:
            return CHINESE_NUMERALS[chinese_num]

        result = 0
        for char in chinese_num:
            if char in CHINESE_NUMERALS:
                result += CHINESE_NUMERALS[char]
        return result if result > 0 else None

    def parse_year_value(segment: str, allow_high_without_context: bool) -> Optional[int]:
        # [^\S\n]* 匹配空白但不含换行，防止跨行匹配（如 "性别：0\n年龄" 中的 0+\n+年）
        for m in re.finditer(r'(?<!\d)(\d{1,2})[^\S\n]*年(?!\s*(?:[代份初底]|应届))', segment):
            val = int(m.group(1))
            context = segment[max(0, m.start() - 8):min(len(segment), m.end() + 8)]
            has_exp_context = any(word in context for word in ('经验', '工作', '从业', '开发', '后端', '前端', '测试', '产品', '项目'))
            if val <= 25 or allow_high_without_context or has_exp_context:
                return val

        chinese_pattern = r'([零一二三四五六七八九十两]+(?:十[一二三四五六七八九两]?)?)[^\S\n]*年'
        for m in re.finditer(chinese_pattern, segment):
            val = parse_chinese_num(m.group(1))
            if val is None:
                continue
            context = segment[max(0, m.start() - 8):min(len(segment), m.end() + 8)]
            has_exp_context = any(word in context for word in ('经验', '工作', '从业', '开发', '后端', '前端', '测试', '产品', '项目'))
            if val <= 25 or allow_high_without_context or has_exp_context:
                return val
        return None

    normalized = text.replace(' ', '')

    # 优先解析明确标注的经验字段，API 摘要常见格式为 "经验：8年"。
    for line in normalized.splitlines():
        if re.search(r'(?:工作年限|工作经验|从业年限|经验)[：:]', line):
            parsed = parse_year_value(line, allow_high_without_context=True)
            if parsed is not None:
                return parsed

    return parse_year_value(normalized, allow_high_without_context=False)


def parse_experience_months(text: str) -> Optional[int]:
    """解析明确工作经验月数；无法确认时返回 None。"""
    if not text:
        return None
    normalized = re.sub(r'\s+', '', text)
    if re.search(r'(?:应届(?:生|毕业生)?|在校生|无工作经验|暂无工作经验)', normalized):
        return 0

    year_month = re.search(r'(\d{1,2})年(?:零)?(\d{1,2})个月', normalized)
    if year_month:
        return int(year_month.group(1)) * 12 + int(year_month.group(2))
    half_year = re.search(r'(\d{1,2})年半', normalized)
    if half_year:
        return int(half_year.group(1)) * 12 + 6
    months = re.search(r'(?<!\d)(\d{1,3})个?月(?:工作|开发|从业|实习)?经验', normalized)
    if months:
        return int(months.group(1))

    years = parse_experience_years(text)
    if years is not None:
        return years * 12

    # 仅在存在明确“工作经历”时间段时计算累计月份；合并重叠月份，实习经历不计入。
    intervals: list[tuple[int, int]] = []
    current_month = date.today().year * 12 + date.today().month
    for line in text.splitlines():
        if not line.strip().startswith("工作经历：") or "实习" in line:
            continue
        dates = re.findall(r'(20\d{2})[./-](\d{1,2})', line)
        if not dates:
            continue
        start = int(dates[0][0]) * 12 + int(dates[0][1])
        end = current_month
        if len(dates) >= 2:
            end = int(dates[1][0]) * 12 + int(dates[1][1])
        if end >= start:
            intervals.append((start, end))
    if not intervals:
        return None
    intervals.sort()
    merged: list[list[int]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return sum(end - start + 1 for start, end in merged)


def _parse_candidate_salary_range(text: str) -> tuple[Optional[int], Optional[int]]:
    """从候选人 summary 提取期望薪资范围，单位 K。支持多行文本扫描。"""
    if not text:
        return None, None
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        if '面议' in line:
            continue

        m = re.search(r'(\d+(?:\.\d+)?)\s*[kK]?\s*[-~～\-]\s*(\d+(?:\.\d+)?)\s*[kK万]?', line)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            # "万" 单位转 K：1-2万 → 10-20K
            if '万' in line[m.start():m.end() + 2]:
                lo, hi = lo * 10, hi * 10
            return int(lo), int(hi)

        m = re.search(r'^(\d+(?:\.\d+)?)\s*[kK薪千]', line)
        if m:
            val = int(float(m.group(1)))
            return val, val

        # API 格式："期望薪资：15K以上" 或 "期望薪资：15K" 或 "期望薪资：15"
        m = re.search(r'(\d+(?:\.\d+)?)\s*[kK薪千]', line)
        if m:
            val = int(float(m.group(1)))
            return val, val

        # 裸数字（"期望薪资：15"）
        m = re.search(r'^(\d+(?:\.\d+)?)$', line.strip())
        if m:
            val = int(float(m.group(1)))
            return val, val

    return None, None


def _keyword_found(text: str, keyword: str) -> bool:
    """检查关键词是否在文本中作为独立词出现，避免英文子串误匹配。"""
    if keyword in _SKILL_ALIASES:
        return _condition_item_found(text, keyword)
    if any('一' <= c <= '鿿' for c in keyword):
        return keyword.lower() in text.lower()
    try:
        return bool(re.search(r'\b' + re.escape(keyword) + r'\b', text, re.IGNORECASE))
    except re.error:
        return keyword.lower() in text.lower()


def _calc_edu_bonus(text: str) -> int:
    """计算学历加分（0~10）"""
    bonus = 0
    has_985211 = any(mark in text for mark in ['985', '211', '双一流'])
    is_doctor = '博士' in text
    is_master = '硕士' in text
    is_bachelor = '本科' in text

    if is_doctor:
        bonus = SCORE_EDU_DOCTOR
    elif is_master:
        bonus = SCORE_EDU_MASTER_985 if has_985211 else SCORE_EDU_MASTER
    elif is_bachelor:
        bonus = SCORE_EDU_BACHELOR_985 if has_985211 else SCORE_EDU_BACHELOR

    return bonus


def _has_non_regular_edu_risk(text: str) -> bool:
    """Return True when text contains words that may indicate non-regular education."""
    return any(ne in text for ne in NON_REGULAR_EDU)


def _has_4year_bachelor_evidence(text: str) -> bool:
    """Return True when text contains evidence of 4-year bachelor degree.

    检查本科段的起止时间是否≥4年，作为统招本科的有力佐证。
    """
    for segment in re.split(r'[\n；;]', text):
        if "本科" not in segment:
            continue
        years = [int(year) for year in re.findall(r'(?<!\d)(19\d{2}|20\d{2})(?!\d)', segment)]
        if len(years) >= 2 and years[-1] - years[-2] >= 4:
            return True
    # 检查是否有明确的"4年制本科"描述
    if re.search(r'4\s*年制本科', text):
        return True
    return False


def _explicit_non_regular_bachelor_reason(text: str) -> str:
    """Return a deterministic rejection reason for a non-regular bachelor path."""
    if any(term in text for term in _EXPLICIT_NON_REGULAR_EDU):
        return "明确为非统招本科"
    if re.search(r'第一学历.{0,12}(?:大专|专科)', text, re.DOTALL):
        return "第一学历为大专/专科，后续本科不符合统招本科要求"
    if re.search(r'(?:大专|专科).{0,80}(?:后修|后续|在读|取得|升读).{0,20}本科', text, re.DOTALL):
        return "大专/专科后取得本科，不符合统招本科要求"
    if re.search(r'(?:2|3|两|二|三)\s*年制本科', text):
        return "本科教育年限不超过3年，不符合统招本科要求"

    for segment in re.split(r'[\n；;]', text):
        if "本科" not in segment:
            continue
        years = [int(year) for year in re.findall(r'(?<!\d)(19\d{2}|20\d{2})(?!\d)', segment)]
        if len(years) >= 2 and 0 < years[-1] - years[-2] <= 3:
            return "本科教育经历不超过3年，不符合统招本科要求"
    return ""


def _add_risk_flag(details: dict[str, Any], flag: str, blocked_reason: str = "需要人工确认") -> None:
    """Attach a manual-review risk flag to filter details."""
    risk_flags = details.setdefault('risk_flags', [])
    if flag not in risk_flags:
        risk_flags.append(flag)
    details['manual_review_required'] = True
    details['qualification_status'] = MANUAL_REVIEW
    reasons = details.setdefault('qualification_reasons', [])
    if flag not in reasons:
        reasons.append(flag)
    details['auto_greet_blocked_reason'] = blocked_reason


def filter_candidate(candidate_text: str, rule: dict[str, Any], structured_fields: dict[str, Any] | None = None) -> tuple[bool, int, dict[str, Any]]:
    """候选人筛选逻辑，返回 (passed, score, details)。"""
    try:
        details: dict[str, Any] = {
            'exp_matched': True,
            'edu_matched': True,
            'required_conditions_matched': True,
            'tech_matched': True,
            'skill_matches': [],
            'skill_total': 0,
            'skill_matched_count': 0,
            'preferred_matches': [],
            'preferred_bonus': 0,
            'exp_bonus': 0,
            'edu_bonus': 0,
            'score_breakdown': {},
            'score_explanation': [],
            'keyword_evidence': [],
            'risk_flags': [],
            'manual_review_required': False,
            'auto_greet_blocked_reason': '',
            'qualification_status': QUALIFIED,
            'qualification_reasons': [],
            'qualification_evidence': [],
        }
        hard_checks: list[str] = []

        edu_bonus = 0
        if rule.get("edu", "不限") != "不限":
            edu_keywords = {"博士": 6, "硕士": 5, "本科": 4, "大专": 3, "高中": 2, "中专": 1}
            candidate_edu_level = max(
                [edu_keywords.get(word, 0) for word in edu_keywords if word in candidate_text],
                default=0,
            )
            required_edu = edu_keywords.get(rule.get("edu", "不限"), 0)
            has_non_regular_risk = _has_non_regular_edu_risk(candidate_text)
            explicit_non_regular_reason = _explicit_non_regular_bachelor_reason(candidate_text)

            if rule.get("edu") == "本科":
                if explicit_non_regular_reason and not re.search(r'(统招|全日制)\s*本科', candidate_text):
                    return False, 0, {
                        "reason": f"学历不符：{explicit_non_regular_reason}",
                        "qualification_status": REJECTED,
                    }
                has_4year_evidence = _has_4year_bachelor_evidence(candidate_text)
                if candidate_edu_level >= 5:
                    # 硕士/博士直接通过学历等级，但仍需检查非统招风险
                    if has_non_regular_risk:
                        _add_risk_flag(details, "学历形式待确认：疑似非统招硕士/博士", "学历形式待确认")
                        hard_checks.append("学历：硕士/博士等级通过，学历形式待人工确认")
                    else:
                        hard_checks.append(f"学历：通过，要求{rule.get('edu')}")
                elif candidate_edu_level == 4:
                    if has_non_regular_risk:
                        if re.search(r'(统招|全日制)\s*本科', candidate_text):
                            hard_checks.append(f"学历：通过，要求{rule.get('edu')}（统招本科）")
                        elif has_4year_evidence:
                            # 有4年起止时间证据，作为统招本科的有力佐证
                            hard_checks.append(f"学历：通过，要求{rule.get('edu')}（4年制本科佐证）")
                        else:
                            _add_risk_flag(details, "学历形式待确认：疑似非统招本科", "学历形式待确认")
                            hard_checks.append("学历：本科等级通过，学历形式待人工确认")
                    else:
                        hard_checks.append(f"学历：通过，要求{rule.get('edu')}")
                elif has_non_regular_risk:
                    if has_4year_evidence:
                        # 有4年起止时间证据，作为统招本科的有力佐证
                        hard_checks.append(f"学历：通过，要求本科（4年制本科佐证）")
                    else:
                        _add_risk_flag(details, "学历形式待确认：疑似非统招本科", "学历形式待确认")
                        hard_checks.append("学历：疑似本科路径，学历形式待人工确认")
                else:
                    return False, 0, {"reason": "学历不足：要求本科"}
            elif required_edu > 0 and candidate_edu_level < required_edu:
                return False, 0, {"reason": f"学历不足：要求{rule.get('edu')}，实际未达要求"}

            edu_bonus = _calc_edu_bonus(candidate_text)
        else:
            hard_checks.append("学历：未设置硬性要求")
        details['edu_bonus'] = edu_bonus

        min_exp = rule.get("min_exp", 0)
        if min_exp > 0:
            # 优先使用 API 提供的结构化经验字段，fallback 到正则解析
            exp_months = None
            if structured_fields and 'exp_years' in structured_fields:
                try:
                    exp_months = int(float(structured_fields['exp_years']) * 12)
                except (TypeError, ValueError):
                    exp_months = None
            else:
                exp_months = parse_experience_months(candidate_text)
            if exp_months is not None:
                if min_exp * 12 > exp_months:
                    actual = f"{exp_months}个月" if exp_months < 12 else f"{exp_months / 12:g}年"
                    return False, 0, {"reason": f"经验不足：要求{min_exp}年，实际{actual}", "qualification_status": REJECTED}
                exp_years = exp_months // 12
                details['exp_bonus'] = min((exp_years - min_exp) * SCORE_EXP_MULTIPLIER, SCORE_EXP_MAX)
                hard_checks.append(f"经验：通过，要求{min_exp}年，实际{exp_years}年，超额加分{details['exp_bonus']}")
            else:
                flag = f"工作经验待确认：未识别明确年限（要求≥{min_exp}年）"
                _add_risk_flag(details, flag, "工作经验待确认")
                hard_checks.append(f"经验：未识别明确年限，转人工确认，要求{min_exp}年")
        else:
            hard_checks.append("经验：未设置硬性要求")

        max_age = rule.get("max_age")
        if max_age is not None:
            # 优先使用 API 提供的结构化年龄字段
            age_val = None
            if structured_fields and 'age' in structured_fields:
                age_val = structured_fields['age']
            else:
                age_match = re.search(r'(?:年龄[：:\s]*)?(\d+)\s*岁', candidate_text)
                age_val = int(age_match.group(1)) if age_match else None
            if age_val is not None and age_val > max_age:
                return False, 0, {"reason": f"年龄不符：要求≤{max_age}岁，实际{age_val}岁"}
            if age_val is not None:
                hard_checks.append(f"年龄：通过，要求≤{max_age}岁，实际{age_val}岁")
            else:
                hard_checks.append(f"年龄：未识别明确年龄，要求≤{max_age}岁")

        required_gender = normalize_gender(rule.get("gender"))
        if required_gender:
            candidate_gender = None
            if structured_fields and structured_fields.get('gender') is not None:
                candidate_gender = normalize_candidate_gender(structured_fields['gender'])
            if candidate_gender is None:
                for line in candidate_text.split('\n'):
                    stripped = line.strip()
                    if stripped.startswith("性别：") or stripped.startswith("性别:"):
                        candidate_gender = normalize_candidate_gender(stripped)
                        break
            if candidate_gender is None:
                flag = (
                    f"性别待确认：岗位要求{required_gender}，候选人性别未识别"
                )
                _add_risk_flag(details, flag, "性别待确认")
                hard_checks.append(
                    f"性别：未识别，转人工确认，要求{required_gender}"
                )
            elif candidate_gender != required_gender:
                return False, 0, {
                    "reason": (
                        f"性别不符：要求{required_gender}，实际{candidate_gender}"
                    ),
                    "qualification_status": REJECTED,
                }
            else:
                hard_checks.append(
                    f"性别：通过，要求{required_gender}，实际{candidate_gender}"
                )
        else:
            hard_checks.append("性别：未设置硬性要求")

        work_location = rule.get("work_location")
        if work_location and work_location.strip():
            required_locations = re.split(r'[/、/]', work_location)
            required_locations = [loc.strip() for loc in required_locations if loc.strip()]

            # 优先使用 API 提供的结构化城市字段（支持多城市）
            candidate_cities: list[str] = []
            if structured_fields and 'city' in structured_fields:
                city_raw = structured_fields['city']
                candidate_cities = [c.strip() for c in re.split(r'[/、,，]', city_raw) if c.strip()]
            else:
                c = _extract_city(candidate_text)
                if c:
                    candidate_cities = [c]

            candidate_city_display = "/".join(candidate_cities) if candidate_cities else ""
            if candidate_cities and required_locations:
                if not any(
                    loc in city
                    for city in candidate_cities
                    for loc in required_locations
                ):
                    return False, 0, {"reason": f"地点不符：要求{work_location}，期望{candidate_city_display}"}
                hard_checks.append(f"地点：通过，要求{work_location}，期望{candidate_city_display}")
            else:
                hard_checks.append(f"地点：未识别明确城市，要求{work_location}")

        salary_max = rule.get("salary_max")
        if rule.get("salary_min") is not None and salary_max is not None:
            # 优先使用 API 提供的结构化薪资字段，fallback 到正则解析
            cand_min_k = None
            if structured_fields and 'salary_min' in structured_fields:
                cand_min_k = structured_fields['salary_min']
            else:
                cand_min_k, _ = _parse_candidate_salary_range(candidate_text)
            if cand_min_k is not None and cand_min_k >= salary_max + 1:
                return False, 0, {"reason": f"薪资不匹配：岗位最高{salary_max}K，候选人期望最低{cand_min_k}K"}
            if cand_min_k is not None:
                hard_checks.append(f"薪资：通过，岗位最高{salary_max}K，候选人期望最低{cand_min_k}K")
            else:
                hard_checks.append(f"薪资：未识别明确期望，岗位最高{salary_max}K")

        # 求职状态检查：明确不考虑的直接淘汰
        job_status = ""
        if structured_fields and structured_fields.get('job_status'):
            job_status = structured_fields['job_status']
        else:
            # 从文本兜底解析
            for line in candidate_text.split('\n'):
                stripped = line.strip()
                if stripped.startswith("求职状态："):
                    job_status = stripped[len("求职状态："):].strip()
                    break
        if job_status:
            if '暂不考虑' in job_status or '不考虑' in job_status:
                return False, 0, {"reason": f"求职状态不符：{job_status}"}
            if '在职' in job_status and '离职' not in job_status:
                # 在职状态不标记为需人工确认，只记录日志
                hard_checks.append(f"求职状态：{job_status}，在职中")
            else:
                hard_checks.append(f"求职状态：{job_status}，通过")

        for condition in rule.get("required_conditions", []):
            cond_result = check_required_condition(candidate_text, condition)
            if not cond_result['passed']:
                return False, 0, {"reason": cond_result['reason']}
            for flag in cond_result.get('risk_flags', []):
                _add_risk_flag(details, flag, "学历形式待确认")
            hard_checks.append(f"必要条件：通过，{condition}")
        details['required_conditions_matched'] = True

        tech_keywords_or = rule.get("tech_conditions", [])
        if tech_keywords_or:
            tech_found = any(_keyword_found(candidate_text, tech) for tech in tech_keywords_or)
            if not tech_found:
                return False, 0, {"reason": f"技术不匹配：需要{tech_keywords_or}中至少一项"}
            hard_checks.append(f"技术条件：通过，{tech_keywords_or} 至少一项")
        details['tech_matched'] = True

        keywords = rule.get("keywords", [])
        skill_score = 0
        total_possible_weight = 0
        matched_skills = []
        keyword_evidence = []

        if keywords:
            for keyword in keywords:
                if isinstance(keyword, dict):
                    kw_name = keyword.get("name", "")
                    kw_weight = keyword.get("weight", 1)
                else:
                    kw_name = keyword
                    kw_weight = 1

                total_possible_weight += kw_weight
                if _keyword_found(candidate_text, kw_name):
                    matched_skills.append(kw_name)
                    skill_score += kw_weight
                    evidence = _find_item_evidence(candidate_text, kw_name)
                    keyword_evidence.append({
                        'type': 'skill',
                        'name': kw_name,
                        'weight': kw_weight,
                        'evidence': evidence
                    })

            details['skill_matched_count'] = len(matched_skills)
            details['skill_matches'] = matched_skills
            details['skill_total'] = total_possible_weight

        if total_possible_weight > 0:
            skill_score_normalized = int((skill_score / total_possible_weight) * SCORE_SKILL_MAX)
        else:
            skill_score_normalized = SCORE_SKILL_MAX

        preferred_bonus = 0
        preferred_matches = []
        for keyword in rule.get("preferred_keywords", []):
            if isinstance(keyword, dict):
                kw_name = keyword.get("name", "")
                kw_bonus = keyword.get("bonus", keyword.get("weight", 1))
            else:
                kw_name = keyword
                kw_bonus = 1
            if kw_name and _condition_item_found(candidate_text, kw_name):
                preferred_matches.append(kw_name)
                preferred_bonus += int(kw_bonus)
                evidence = _find_item_evidence(candidate_text, kw_name)
                keyword_evidence.append({
                    'type': 'preferred',
                    'name': kw_name,
                    'weight': int(kw_bonus),
                    'evidence': evidence
                })
        preferred_bonus = min(preferred_bonus, _PREFERRED_BONUS_MAX)
        details['preferred_matches'] = preferred_matches
        details['preferred_bonus'] = preferred_bonus

        score = SCORE_BASE + skill_score_normalized + details['exp_bonus'] + details['edu_bonus'] + preferred_bonus
        score = min(score, 100)
        details['keyword_evidence'] = keyword_evidence
        details['score_breakdown'] = {
            'base': SCORE_BASE,
            'skill': skill_score_normalized,
            'experience': details['exp_bonus'],
            'education': details['edu_bonus'],
            'preferred': preferred_bonus,
            'total': score
        }
        details['score_explanation'] = [
            "【评分构成】",
            f"基础分：{SCORE_BASE}",
            f"技能分：{skill_score_normalized}/{SCORE_SKILL_MAX}，命中 {len(matched_skills)} 个关键词，权重 {skill_score}/{total_possible_weight}",
            f"经验加分：{details['exp_bonus']}/{SCORE_EXP_MAX}",
            f"学历加分：{details['edu_bonus']}",
            f"优先项加分：{preferred_bonus}/{_PREFERRED_BONUS_MAX}",
            "",
            "【硬条件检查】",
            *hard_checks
        ]
        return True, score, details

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception("筛选异常: %s", e)
        return False, 0, {"reason": f"筛选异常 ({type(e).__name__}): {str(e)[:50]}"}


def check_required_condition(candidate_text: str, condition: str | dict[str, Any]) -> dict[str, Any]:
    """检查单个必要条件。"""
    if isinstance(condition, str):
        if condition == "统招本科":
            if re.search(r'(统招|全日制)\s*本科', candidate_text):
                return {"passed": True, "reason": ""}

            has_bachelor = "本科" in candidate_text
            explicit_non_regular_reason = _explicit_non_regular_bachelor_reason(candidate_text)
            if explicit_non_regular_reason:
                return {
                    "passed": False,
                    "reason": f"学历不符：{explicit_non_regular_reason}",
                }
            if has_bachelor or "硕士" in candidate_text or "博士" in candidate_text or "专升本" in candidate_text:
                return {
                    "passed": True,
                    "reason": "",
                    "risk_flags": ["学历形式待确认：未发现明确统招本科证据"],
                    "manual_review_required": True,
                }
            return {"passed": False, "reason": f"必要条件不满足：{condition}"}

        if not _condition_item_found(candidate_text, condition):
            return {"passed": False, "reason": f"必要条件不满足：{condition}"}
        return {"passed": True, "reason": ""}

    elif isinstance(condition, dict):
        cond_type = condition.get("type", "or")
        items = condition.get("items", [])

        if not items:
            return {"passed": True, "reason": ""}

        if cond_type == "or":
            matched = any(_condition_item_found(candidate_text, item) for item in items)
            if not matched:
                return {"passed": False, "reason": f"必要条件不满足：需要{items}中至少一项"}
            return {"passed": True, "reason": ""}

        elif cond_type == "and":
            for item in items:
                if not _condition_item_found(candidate_text, item):
                    return {"passed": False, "reason": f"必要条件不满足：缺少{item}"}
            return {"passed": True, "reason": ""}

    return {"passed": True, "reason": ""}


def evaluate_candidate(candidate_text: str, rule: dict[str, Any]) -> bool:
    """兼容旧版本的布尔筛选接口。"""
    passed, score, details = filter_candidate(candidate_text, rule)
    return passed
