# -*- coding: utf-8 -*-
"""
招聘需求文档解析器
支持解析：
1. 必要条件（硬性约束）- 必须全部满足（技术关键词只需满足其一）
2. 职位要求（软性要求）- 用于评分，匹配越多得分越高
"""
import json
import logging
import os
import re
import unicodedata
from typing import Dict
from constants import MAJOR_CITIES, CHINESE_NUMERALS

logger = logging.getLogger(__name__)

_major_cities_set = set(MAJOR_CITIES)


SKILL_ALIASES = {
    'Spring Cloud': ['Spring Cloud', 'SpringCloud'],
    'Spring Boot': ['Spring Boot', 'SpringBoot'],
    'Spring MVC': ['Spring MVC', 'SpringMvc'],
    'Spring AI': ['Spring AI', 'SpringAI'],
    'MyBatis Plus': ['MyBatis Plus', 'MyBatisPlus'],
    'MyBatis': ['MyBatis'],
    'Node.js': ['Node.js', 'NodeJs', 'Node'],
    'Vue.js': ['Vue.js', 'VueJs', 'Vue'],
    'AI Agent': ['AI Agent', 'AIAgent', 'Al Agent', 'AlAgent', 'Agent', '智能体', '大模型Agent', '大模型 Agent'],
    'LangChain': ['LangChain', 'Langchain'],
    'Kubernetes': ['Kubernetes', 'K8s'],
    'PostgreSQL': ['PostgreSQL', 'Postgres'],
    'SQL': ['SQL', 'sql'],
}

TECH_SKILLS = [
    # Spring 家族
    'Spring Cloud', 'Spring Boot', 'Spring MVC', 'Spring AI', 'Spring',
    # 数据库
    'MySQL', 'PostgreSQL', 'MongoDB', 'Redis', 'Oracle', 'SQLServer', 'SQLite', 'Elasticsearch', 'SQL',
    # 语言
    'JavaScript', 'TypeScript', 'Java', 'Python', 'Go', 'Rust', 'C++', 'C#', 'PHP', 'Ruby', 'Swift', 'Kotlin', 'Scala',
    # 前端
    'React', 'Vue.js', 'Angular', 'HTML', 'CSS', 'Sass', 'Less', 'jQuery', 'Bootstrap', 'Webpack', 'Vite', 'Node.js', 'Next.js',
    # 后端框架
    'Django', 'Flask', 'Express', 'FastAPI', 'Gin', 'Laravel', 'Dubbo', 'MyBatis Plus', 'MyBatis',
    # 云/DevOps
    'AWS', 'Azure', '阿里云', '腾讯云', '华为云',
    'Docker', 'Kubernetes', 'Jenkins', 'GitLab', 'CI/CD', 'Terraform', 'Ansible', 'Nginx', 'Apache',
    # 数据/AI
    'TensorFlow', 'PyTorch', 'Pandas', 'NumPy', 'Spark', 'Hadoop',
    '机器学习', '深度学习', '数据分析', '数据挖掘', '数据可视化',
    'LLM', '大模型', 'AI Agent', 'LangChain', '智能问答', '知识库', 'RAG', '向量数据库',
    '爬虫', '网络爬虫',
    # 金融/量化
    '量化', '因子模型',
    # 爬虫框架
    'Scrapy', 'Selenium',
    # 其他
    'Linux', 'Git', '微服务', '分布式', '大数据', '云计算',
    'MQTT', 'Kafka', 'RabbitMQ', '消息队列', '消息中间件', 'GraphQL', 'RESTful', 'RPC',
    # 工作流
    'activiti', 'camunda', 'flowable', '工作流',
]

INDUSTRY_CATEGORIES = {
    '债券': ['债券', '固收', '固定收益', '利率债', '信用债', '国债', '公司债', '企业债', '可转债'],
    '基金': ['基金', '公募', '私募', 'ETF', 'FOF', '货币基金', '债券基金', '股票基金'],
    '期货': ['期货', '商品期货', '金融期货', '股指期货', 'CTA'],
    '期权': ['期权', '衍生品', '结构化产品', '场外期权', '场内期权'],
    '量化': ['量化', '量化交易', '量化策略', '量化模型', '因子', 'alpha'],
    '证券': ['证券', '券商', '证券公司', '证券交易'],
}

CERT_KEYWORDS = [
    '证券从业资格', '基金从业资格', '期货从业资格', '银行从业资格',
    'CFA', 'CPA', 'FRM', 'ACCA', 'CIIA',
    '法律职业资格', '司法资格', '注册会计师', '税务师',
    'PMP', 'PRINCE2', 'Scrum Master',
]

BONUS_DOMAIN_KEYWORDS = [
    '证券', '基金', '期货', '银行', '保险', '信托', '资管', '资产管理',
    '固收', '固定收益', '量化', '衍生品', '期权', '外汇', '大宗商品',
    '金融', '投行', '投资', '私募', '风控', '合规',
    '游戏', '电商', '物联网', '区块链', '芯片', '嵌入式',
    '医疗', '医药', '生物', '教育', '通信', '汽车', '自动驾驶',
]

HARD_HINT_RE = re.compile(r'必须|必需|要求|具备|需要|需|不低于|不少于|至少|硬性|必备|任职资格')
# 强硬条件指示词（独立出现即可判 hard，不需要 section 上下文）
HARD_STRONG_RE = re.compile(r'必须|必需|不低于|不少于|至少|硬性|必备|一票否决')
# 弱硬条件指示词（仅在 hard section 内，或与学历/年龄等硬字段共现时才判 hard）
HARD_WEAK_RE = re.compile(r'要求|具备|需要|需|任职资格')
PREFERRED_HINT_RE = re.compile(r'优先|加分|更佳|优先考虑|优先录用')
SKILL_HINT_RE = re.compile(r'熟悉|熟练|掌握|精通|了解|参与过|使用|开发|运维|建设')
OR_HINT_RE = re.compile(r'或|或者|任一|其一|至少一种|至少一项|包括但不限于|等|[/／]')
AND_HINT_RE = re.compile(r'同时|均需|全部|均要|且|并且')
REMOTE_LOCATION_RE = re.compile(r'远程|居家办公|线上办公|全国|不限地点|地点不限|不限制地点|混合办公')

# 段落标题匹配（_structured_lines 使用）
_SECTION_HARD_RE = re.compile(r'硬性条件|硬性要求|基本条件|必备条件|必须满足|必须条件|必要条件|任职资格|应聘条件|岗位基本条件|基本要求')
_SECTION_DESC_RE = re.compile(r'职位描述|岗位职责|工作内容|主要职责|工作职责')
_SECTION_REQ_RE = re.compile(r'职位要求|任职要求|岗位要求|应聘要求|能力要求|我们希望你|期望你|我们期待')
_SECTION_PREFERRED_RE = re.compile(r'软性条件|加分项|优先条件|加分条件|优先考虑|优先录用')
_SUB_HEADING_TO_HARD_RE = re.compile(r'工作经验|学历要求|年龄要求|技能要求|专业要求|证书要求')


def _clean_job_title(title: str) -> str:
    """清理岗位标题中的序号/标签前缀和多余空白。"""
    title = re.sub(r'\s+', ' ', str(title or '')).strip()
    title = re.sub(r'^(?:岗位|职位|招聘)\s*\d+\s*[：:、.\-]\s*', '', title)
    title = re.sub(r'^\d+\s*[：:、.\-]\s*', '', title)
    return title.strip()


def _strip_list_marker(line: str) -> str:
    """去掉编号、项目符号、Markdown 标记，保留句子内容。"""
    line = re.sub(r'^\s*(?:[-*•·●■□]+|\d+[).、．:：-]+|[一二三四五六七八九十]+[、.．])\s*', '', line)
    return line.strip()


def _structured_lines(text: str) -> list[dict]:
    """将招聘文本转换为行级结构，供后续字段抽取和条件分类复用。"""
    sections = {
        'hard': _SECTION_HARD_RE,
        'desc': _SECTION_DESC_RE,
        'req': _SECTION_REQ_RE,
        'preferred': _SECTION_PREFERRED_RE,
    }
    current = 'body'
    rows = []
    for raw_line in text.split('\n'):
        raw = raw_line.strip()
        if not raw:
            continue
        heading = re.sub(r'^[#\s]+', '', raw).strip(' :：')
        matched_section = None
        for section, pattern in sections.items():
            if pattern.fullmatch(heading) or pattern.search(heading):
                matched_section = section
                break
        # markdown 子标题处理：如果主 sections 未匹配，检查是否为硬条件子标题
        _sub_m = _SUB_HEADING_TO_HARD_RE.search(heading) if matched_section is None else None
        if _sub_m and heading == _sub_m.group(0):
            if current in ('body', 'desc'):
                matched_section = 'hard'
            # 如果已在 hard/req/preferred section 内，子标题不改变 section
        if matched_section:
            current = matched_section
            continue

        clean = _strip_list_marker(raw)
        if not clean:
            continue
        rows.append({
            "raw": raw,
            "text": clean,
            "section": current,
            "kind": _classify_requirement_line(clean, current),
        })
    return rows


def _classify_requirement_line(line: str, section: str = "") -> str:
    """粗分类单行需求：硬条件、优先项、普通技能或其他。

    分类策略：
    - preferred section 或含优先/加分关键词 → preferred
    - hard section 内 → hard（section 本身即强信号）
    - req section 内：强指示词 → hard；弱指示词 + 无技能词 → hard；否则按技能词判断
    - body section：仅强指示词 → hard，其余按技能词判断
    - desc section：按技能词判断
    """
    # 1. 优先项：section 或关键词命中
    if section == 'preferred' or PREFERRED_HINT_RE.search(line):
        return 'preferred'
    # 2. 描述段：按技能词判断
    if section == 'desc':
        return 'skill' if (SKILL_HINT_RE.search(line) or _find_terms(line, TECH_SKILLS)) else 'other'
    # 3. 硬条件段：section 本身即硬条件信号，不再二次检查关键词
    if section == 'hard':
        return 'hard'
    # 4. 要求段：区分硬条件和技能要求
    if section == 'req':
        has_strong = bool(HARD_STRONG_RE.search(line))
        has_weak = bool(HARD_WEAK_RE.search(line))
        has_skill = bool(SKILL_HINT_RE.search(line) or _find_terms(line, TECH_SKILLS))
        if has_strong:
            return 'hard'
        if has_weak and not has_skill:
            return 'hard'
        if has_skill:
            return 'skill'
        return 'other'
    # 5. body 或其他：仅强指示词判 hard，避免"需要/要求"等高频词误判
    if HARD_STRONG_RE.search(line):
        return 'hard'
    if SKILL_HINT_RE.search(line) or _find_terms(line, TECH_SKILLS):
        return 'skill'
    return 'other'


def _canonical_skill_name(name: str) -> str:
    key = re.sub(r'\s+', '', str(name or '')).lower()
    for canonical, aliases in SKILL_ALIASES.items():
        for alias in aliases:
            if key == re.sub(r'\s+', '', alias).lower():
                return canonical
    return str(name or '').strip()


def skill_identity_key(name: str) -> str:
    """Return a stable identity for skill aliases and formatting variants."""
    canonical = _canonical_skill_name(name)
    return re.sub(r'[\s._/\-]+', '', canonical).lower()


def _find_terms(text: str, terms: list[str]) -> list[str]:
    """在文本中查找词典项，返回 canonical 去重结果。"""
    found = []
    seen = set()
    normalized_text = _normalize_skill_text(text)
    for term in sorted(terms, key=lambda x: len(re.sub(r'\s+', '', x)), reverse=True):
        canonical = _canonical_skill_name(term)
        aliases = SKILL_ALIASES.get(canonical, [term])
        if any(_term_in_text(alias, text, normalized_text) for alias in aliases):
            key = re.sub(r'\s+', '', canonical).lower()
            if key not in seen:
                found.append(canonical)
                seen.add(key)
    return found


def _term_in_text(term: str, original_text: str, normalized_text: str | None = None) -> bool:
    normalized_text = normalized_text if normalized_text is not None else _normalize_skill_text(original_text)
    normalized_term = re.sub(r'\s+', '', term)
    if re.match(r'^[A-Za-z0-9+#/.]+$', normalized_term):
        pat = r'(?<![A-Za-z0-9_])' + re.escape(normalized_term) + r'(?![A-Za-z0-9_])'
        return bool(re.search(pat, normalized_text, re.IGNORECASE))
    return term.lower() in original_text.lower() or normalized_term.lower() in normalized_text.lower()


def _normalize_skill_text(text: str) -> str:
    normalized = text
    for canonical, aliases in SKILL_ALIASES.items():
        compact = re.sub(r'\s+', '', canonical)
        for alias in aliases:
            normalized = re.sub(re.escape(alias), compact, normalized, flags=re.IGNORECASE)
    return normalized


def _find_industry_terms(text: str) -> list[str]:
    found = []
    for main_kw, aliases in INDUSTRY_CATEGORIES.items():
        if any(alias.lower() in text.lower() for alias in aliases):
            found.append(main_kw)
    return found


def _preferred_clause_text(line: str) -> str:
    """只保留带“优先/加分”的子句，避免整行技能被优先语境污染。"""
    clauses = re.split(r'[；;。！？!?]', _strip_list_marker(line or ""))
    preferred_clauses = []
    for clause in clauses:
        clause = clause.strip()
        if not PREFERRED_HINT_RE.search(clause):
            continue
        comma_parts = [part.strip() for part in re.split(r'[,，]', clause) if part.strip()]
        if len(comma_parts) > 1 and PREFERRED_HINT_RE.search(comma_parts[-1]):
            preferred_clauses.append(comma_parts[-1])
        else:
            preferred_clauses.append(clause)
    return "；".join(preferred_clauses)


def _normalize_preferred_name(name: str) -> str:
    cleaned = _strip_list_marker(name)
    cleaned = re.sub(r'^(?:有|具备|使用)', '', cleaned).strip(' ，,、；;。.-')
    cleaned = re.sub(r'(?:行业)?(?:从业|相关)$', '', cleaned).strip()
    if re.search(r'(?:大模型|AI)', cleaned, re.IGNORECASE) and re.search(r'agent', cleaned, re.IGNORECASE):
        return 'AI Agent'
    terms = _find_terms(cleaned, TECH_SKILLS)
    if len(terms) == 1:
        return terms[0]
    return cleaned


def _unique_cities(text: str) -> list[str]:
    matches = []
    for city in MAJOR_CITIES:
        pos = text.find(city)
        if pos >= 0:
            matches.append((pos, city))
    return [city for _, city in sorted(matches)]


def _format_locations(cities: list[str]) -> str:
    return "/".join(cities)


def _is_preferred_context(text: str) -> bool:
    return bool(PREFERRED_HINT_RE.search(text or ""))


def _remove_education_preferred_phrases(text: str) -> str:
    cleaned = text or ""
    patterns = [
        r'985\s*[/／、,，和及或]*\s*211\s*(?:院校|高校|大学)?\s*(?:优先|加分|更佳|优先考虑)',
        r'211\s*[/／、,，和及或]*\s*985\s*(?:院校|高校|大学)?\s*(?:优先|加分|更佳|优先考虑)',
        r'(?:985|211|双一流)\s*(?:院校|高校|大学)?\s*(?:优先|加分|更佳|优先考虑)',
        r'(?:硕士|研究生|博士|博士后)(?:学历)?(?:及以上)?\s*(?:优先|加分|更佳|优先考虑)',
    ]
    for pattern in patterns:
        cleaned = re.sub(pattern, '', cleaned)
    return cleaned


def _hard_education_text(required_section: str, structured_rows: list[dict]) -> str:
    lines = []
    for line in required_section.split('\n'):
        clean = _strip_list_marker(line)
        clean = _remove_education_preferred_phrases(clean)
        if clean.strip():
            lines.append(clean)
    for row in structured_rows:
        if row["kind"] == "hard" or row["section"] == "hard":
            clean = _remove_education_preferred_phrases(row["text"])
            if clean.strip():
                lines.append(clean)
    return "\n".join(lines)


def _make_group_condition(items: list[str], line: str, category: str) -> dict | None:
    clean_items = []
    seen = set()
    for item in items:
        value = str(item).strip()
        key = value.lower()
        if value and key not in seen:
            clean_items.append(value)
            seen.add(key)
    if not clean_items:
        return None
    cond_type = "and" if AND_HINT_RE.search(line) and not OR_HINT_RE.search(line) else "or"
    return {"type": cond_type, "items": clean_items, "category": category}


def _extract_special_experience_conditions(lines: list[dict]) -> list[dict]:
    """提取专项经验，如“其中3年以上金融行业经验”“2年以上 Python 经验”。"""
    conditions = []
    seen = set()
    exp_pat = re.compile(r'(?:其中)?\s*(\d+|[零一二两三四五六七八九十]+)\s*年以上?([\u4e00-\u9fa5A-Za-z0-9+#/. ]{2,30}?)(?:经验|背景|经历)')
    for row in lines:
        if row["kind"] not in {"hard", "skill"}:
            continue
        for match in exp_pat.finditer(row["text"]):
            years = _chinese_or_int(match.group(1))
            domain_text = match.group(2).strip(' ，,、；;。.-')
            if not years or not domain_text or re.search(r'工作|相关|以上|不少于|不低于', domain_text):
                continue
            terms = _find_terms(domain_text, TECH_SKILLS) + _find_industry_terms(domain_text)
            items = terms or [domain_text]
            label_items = [f"{item}经验≥{years}年" for item in items]
            condition = {"type": "or", "items": label_items, "category": "专项经验"}
            key = ",".join(label_items).lower()
            if key not in seen:
                conditions.append(condition)
                seen.add(key)
    return conditions


def _chinese_or_int(value: str) -> int:
    if value in CHINESE_NUMERALS:
        return CHINESE_NUMERALS[value]
    try:
        return int(value)
    except ValueError:
        return 0


# 预构建字符转换表，避免每次调用时重复创建
_FULLWIDTH_TRANS_TABLE = str.maketrans({
    **{chr(i): chr(i - 0xFF10 + ord('0')) for i in range(0xFF10, 0xFF1A)},  # 全角数字 0-9
    **{chr(i): chr(i - 0xFF21 + ord('A')) for i in range(0xFF21, 0xFF3B)},  # 全角大写字母 A-Z
    **{chr(i): chr(i - 0xFF41 + ord('a')) for i in range(0xFF41, 0xFF5B)},  # 全角小写字母 a-z
    '　': ' ',  # 全角空格
    '－': '-', '：': ':', '～': '~',  # 全角标点
    '（': '(', '）': ')', '，': ',',
})


def _preprocess_text(text: str) -> str:
    """文本预处理：全角转半角、去零宽字符、去 emoji、统一空白"""
    if not text:
        return text
    # 1. 全角数字/字母/空格/标点 → 半角
    text = text.translate(_FULLWIDTH_TRANS_TABLE)

    # 2. 去零宽字符（从网页/微信复制时常见）
    text = re.sub(r'[​‌‍﻿⁠]', '', text)

    # 3. 去 emoji（Unicode Category 以 So 为主，排除常用符号）
    text = ''.join(ch for ch in text if unicodedata.category(ch) not in ('So', 'Sk') or ch in '°±×÷')

    # 4. 连续空白压缩（保留换行，压缩行内空格和 tab）
    text = re.sub(r'[^\S\n]+', ' ', text)

    return text


def _resolve_city(raw: str) -> str:
    """从原始地点字符串中提取城市名，如 '南京市雨花区凯润大厦' → '南京'"""
    if not raw:
        return ""
    # 先从带"市"后缀的格式提取：南京市→南京，上海市→上海
    m = re.search(r'([一-龥]{2,3})市', raw)
    if m:
        candidate = m.group(1)
        if candidate in _major_cities_set:
            return candidate
    # 直接匹配城市名（按长度降序，防止"吉林"误匹配"吉林市"）
    for city in MAJOR_CITIES:
        if city in raw:
            return city
    return ""


def _extract_work_location(text: str) -> str:
    """从招聘需求中提取工作地点（城市名）"""
    if not text:
        return ""
    text = _preprocess_text(text)
    # 地点相关关键词（用于模式匹配和兜底优先级）
    _loc_keywords = r'(?:工作地点|工作城市|工作地|办公地|办公地点|入职城市|派驻|base\s*地?|坐标)'

    # 模式1: "工作地点：南京/上海"、"base 南京，可接受上海" 等
    m = re.search(rf'{_loc_keywords}\s*[：:]?\s*([^\n]{{2,80}})', text, re.IGNORECASE)
    if m:
        loc_text = m.group(1)
        cities = _unique_cities(loc_text)
        if cities:
            return _format_locations(cities)
        if REMOTE_LOCATION_RE.search(loc_text):
            return ""

    if REMOTE_LOCATION_RE.search(text) and not _unique_cities(text):
        return ""

    # 兜底：优先扫描地点关键词附近的行，而非盲目全文扫描
    # 避免"公司总部在上海，本次在成都招聘"误匹配上海
    _hq_keywords = re.compile(r'总部|公司位于|集团位于|坐落于')

    for line in text.split('\n'):
        # 排除总部/公司所在地相关行
        if _hq_keywords.search(line):
            continue
        if re.search(_loc_keywords, line, re.IGNORECASE) or REMOTE_LOCATION_RE.search(line):
            cities = _unique_cities(line)
            if cities:
                return _format_locations(cities)
            if REMOTE_LOCATION_RE.search(line):
                return ""

    for line in text.split('\n'):
        if _hq_keywords.search(line):
            continue
        cities = _unique_cities(line)
        if cities:
            return _format_locations(cities)

    # 最后手段：全文扫描（无分行排除）
    cities = _unique_cities(text)
    if cities:
        return _format_locations(cities)
    return ""


def _extract_gender_requirement(text: str) -> str:
    """Extract only an explicit gender requirement; never infer from the role."""
    if not text:
        return "不限"
    normalized = _preprocess_text(text)
    for line in normalized.split('\n'):
        stripped = line.strip()
        if not stripped:
            continue
        if re.search(r'男女不限|性别不限|不限性别|男(?:性)?女(?:性)?均可', stripped):
            return "不限"
        labeled = re.search(
            r'(?:性别要求|候选人性别|性别)\s*[：:]\s*([^，,。；;\s]+)',
            stripped,
        )
        if labeled:
            value = labeled.group(1)
            if value in {"男", "男性"}:
                return "男"
            if value in {"女", "女性"}:
                return "女"
            if "不限" in value or "均可" in value:
                return "不限"
        restricted = re.search(
            r'(?:仅限|只限|限招|只招|要求)\s*(男性|女性|男|女)'
            r'(?:候选人)?(?:\s|$|[，,。；;])',
            stripped,
        )
        if restricted:
            return "男" if restricted.group(1) in {"男", "男性"} else "女"
    return "不限"


def _extract_salary_range(text: str):
    """从招聘需求中提取薪资范围。返回 (min_k, max_k)，未匹配或面议返回 (None, None)

    支持格式：
    - 标签前缀：薪资/薪酬/待遇/月薪/底薪 + 冒号 + 范围
    - 年薪制：年薪/年包 X-Y万 → 自动转换为月薪 K
    - 无标签范围：15-25K / 15k-25k / 15000-25000元（需附近出现薪资关键词）
    - 单边下限：15K起 / 不低于15K / 保底15K
    - 面议变体：面议/薪资open/待遇从优/面谈
    """
    if not text:
        return None, None
    text = _preprocess_text(text)

    # 边界情况：面议/可谈 等非数字薪资描述，按行检测避免误杀
    for line in text.split('\n'):
        if re.search(r'薪资|薪酬|待遇|月薪|年薪', line):
            if re.search(r'面议|可谈|open|面谈|从优', line, re.IGNORECASE):
                return None, None

    # --- 有标签前缀的模式（高优先级）---
    labeled_prefix = r'(?:薪资(?:范围)?|薪酬|待遇|月薪|底薪|工资)'

    # --- 分级薪资（必须优先于标准模式，否则标准模式会先匹配第一个范围就返回）---
    # 薪资范围：中级：14K-17K 高级：18K-22K → 取全局 min/max = (14, 22)
    _tiered_pat = (
        labeled_prefix
        + r'[^0-9\n]{0,15}'        # prefix 后的非数字文本（如"中级："），限 15 字符防跨行
        + r'(\d+)\s*[kK]?\s*[-~～\-]\s*(\d+)\s*[kK]'   # 第一个范围
        + r'(?:[·•]\d+薪)?'         # 多月薪后缀（可选）
        + r'((?:[^0-9\n]{0,15}\d+\s*[kK]?\s*[-~～\-]\s*\d+\s*[kK](?:[·•]\d+薪)?){1,3})'  # 后续 1-3 个范围
    )
    _range_pat = r'(\d+)\s*[kK]?\s*[-~～\-]\s*(\d+)\s*[kK]'
    m = re.search(_tiered_pat, text)
    if m:
        all_mins = [int(m.group(1))]
        all_maxs = [int(m.group(2))]
        for rm in re.finditer(_range_pat, m.group(3)):
            all_mins.append(int(rm.group(1)))
            all_maxs.append(int(rm.group(2)))
        return min(all_mins), max(all_maxs)

    labeled_patterns = [
        # 薪资范围：15k-25k / 薪酬：15K-25K / 薪资：15-25K（首个K可省略）
        # 支持多月薪格式：20-35K·15薪 / 20-35k·16薪
        labeled_prefix + r'\s*[：:]?\s*(\d+)\s*[kK]?\s*[-~～\-]\s*(\d+)\s*[kK](?:[·•]\d+薪)?',
        # 薪资：15k至25k / 薪资15至25k（首个K可省略）
        labeled_prefix + r'\s*[：:]?\s*(\d+)\s*[kK]?\s*[至到]\s*(\d+)\s*[kK]',
        # 薪资：15000-25000 / 薪酬：12000-18000元（无K后缀，需4位以上数字）
        labeled_prefix + r'\s*[：:]\s*(\d{4,})\s*[-~～\-]\s*(\d{4,})',
        # 年薪20-35万 → 转月薪
        r'(?:年薪|年包)\s*[：:]?\s*(\d+)\s*[-~～\-]\s*(\d+)\s*万',
        # 年薪30万 → 转月薪
        r'(?:年薪|年包)\s*[：:]?\s*(\d+)\s*万',
    ]
    for pat in labeled_patterns:
        m = re.search(pat, text)
        if m:
            groups = m.groups()
            if len(groups) == 2 and groups[1] is not None:
                a, b = int(groups[0]), int(groups[1])
                if '年薪' in pat or '年包' in pat:
                    return a * 10 // 12, b * 10 // 12
                return a, b
            elif len(groups) == 1 or (len(groups) == 2 and groups[1] is None):
                val = int(groups[0])
                if '年薪' in pat or '年包' in pat:
                    m_val = val * 10 // 12
                    return m_val, m_val
                return val, val

    # --- 无标签的范围（需附近有薪资关键词，避免匹配"3-5年"等非薪资数字）---
    no_label_patterns = [
        # 15-25K / 15k-25k（首个K可省略，末尾必须有K）
        r'(?=.*(?:薪资|薪酬|待遇|月薪|底薪|工资))(\d+)\s*[kK]?\s*[-~～\-]\s*(\d+)\s*[Kk]',
        # 15000-25000元
        r'(?=.*(?:薪资|薪酬|待遇|月薪|底薪|工资))(\d{4,})\s*[-~～\-]\s*(\d{4,})\s*(?:元|块)',
    ]
    for pat in no_label_patterns:
        m = re.search(pat, text)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            return a, b

    # --- 单边下限 ---
    min_only_patterns = [
        # 15K起 / 15k以上 / 不低于15K / 保底15K / 起薪15K
        r'(?:起|以上|不低于|保底|起薪|至少)\s*(\d+)\s*[kK]?',
        # 15K起 / 15000以上 / 15K以上
        r'(\d+)\s*[kK]?\s*(?:起|以上)',
        # 不低于15K / 保底15000 / 起薪15K
        r'(?:不低于|保底|起薪|至少)\s*(\d+)',
    ]
    for pat in min_only_patterns:
        m = re.search(pat, text)
        if m:
            return int(m.group(1)), None

    return None, None


def _find_source_line(text: str, keyword: str) -> str:
    """在文本中找到包含关键词的行，返回该行（去列表标记后）。

    支持归一化匹配：忽略空格差异（"Spring Boot" 匹配 "SpringBoot"）、
    大小写、别名（"AI Agent" 匹配 "Al Agent"）。找不到返回空串。
    """
    keyword_lower = keyword.lower()
    keyword_compact = re.sub(r'\s+', '', keyword_lower)
    # 收集所有需要匹配的变体（原词 + 别名）
    canonical = _canonical_skill_name(keyword)
    aliases = SKILL_ALIASES.get(canonical, [keyword])
    match_variants = {keyword_lower, keyword_compact}
    for alias in aliases:
        alias_lower = alias.lower()
        match_variants.add(alias_lower)
        match_variants.add(re.sub(r'\s+', '', alias_lower))

    for line in text.split('\n'):
        clean = _strip_list_marker(line).strip()
        if not clean:
            continue
        clean_lower = clean.lower()
        clean_compact = re.sub(r'\s+', '', clean_lower)
        # 任一变体匹配即返回
        for variant in match_variants:
            if variant in clean_lower or variant in clean_compact:
                return clean
    return ""


def _find_pattern_source_line(text: str, pattern: re.Pattern) -> str:
    """在文本中找到匹配正则的行，返回该行。找不到返回空串。"""
    for line in text.split('\n'):
        if pattern.search(line):
            return _strip_list_marker(line).strip()
    return ""


def parse_job_requirements(text: str) -> Dict:
    """
    从招聘需求文本中解析出关键信息
    """
    text = _preprocess_text(text)
    structured_rows = _structured_lines(text)

    # === 1. 提取职位名称 ===
    job_title = "Java 工程师"  # 默认值

    # 岗位名称后缀（匹配越多越通用）
    _title_suffix = r'工程师|开发|架构|专家|经理|总监|研发|负责人|分析师|设计师|运维|测试|DBA|产品'

    # 支持多种格式匹配岗位名称
    title_patterns = [
        # markdown 标题格式：# 高级 Python/Java 开发工程师
        rf'#\s*(.*({_title_suffix}))',
        # 职位描述【高级 Java/Python 工程师】
        rf'职位描述\s*[\[【〔(（]\s*(.*?(?:{_title_suffix}))\s*[\]】〕)）]',
        # 【高级 Java/Python 工程师】（没有"职位描述"前缀）
        rf'^[\[【〔(（]\s*(.*?(?:{_title_suffix}))\s*[\]】〕)）]',
        # 职位：高级 Java/Python 工程师
        rf'职位\s*[：:]\s*(.*?(?:{_title_suffix}))',
        # 岗位：高级 Java/Python 工程师
        rf'岗位\s*[：:]\s*(.*?(?:{_title_suffix}))',
        # 诚聘：高级 Java/Python 工程师
        rf'诚聘\s*[：:]\s*(.*?(?:{_title_suffix}))',
        # 职位名称：高级 Java 工程师
        rf'职位名称\s*[：:]\s*(.*?(?:{_title_suffix}))',
        # 招聘：高级 Java 工程师 / 岗位名称：DBA
        rf'(?:招聘|岗位名称)\s*[：:]\s*(.*?(?:{_title_suffix}))',
    ]

    for pattern in title_patterns:
        match = re.search(pattern, text, re.MULTILINE)
        if match:
            matched = match.group(1).strip()
            if matched:
                job_title = _clean_job_title(matched)
                break

    # 兜底：首行含严格职位名称后缀（工程师/分析师/架构师等），排除"开发"等动词性词尾防止误匹配正文
    if job_title == "Java 工程师":
        _strict_title_suffix = r'工程师|分析师|架构师|设计师|研究员|顾问|专家|经理|总监|负责人|DBA|产品经理'
        for line in text.split('\n'):
            line = line.strip()
            if line and len(line) <= 30 and re.search(_strict_title_suffix, line):
                job_title = _clean_job_title(line)
                break

    # === 2. 分离不同部分 ===
    required_section = ""
    job_desc_section = ""
    position_req_section = ""

    # 段落标题关键词（支持多种写法）
    _hard_keywords = r'(?:硬性条件|硬性要求|基本条件|必备条件|必须满足|必须条件)'
    _necessary_keywords = r'(?:必要条件|必须条件)'
    _desc_keywords = r'(?:职位描述|岗位职责|工作内容|主要职责|工作职责)'
    _req_keywords = r'(?:职位要求|任职要求|岗位要求|应聘要求|能力要求|我们希望你|任职要求)'
    _soft_keywords = r'(?:软性条件|加分项|优先条件|加分条件|优先考虑)'

    # 先尝试用 markdown 格式分离
    _hard_md_pat = rf'^##\s*{_hard_keywords}'
    if re.search(_hard_md_pat, text, re.MULTILINE):
        parts = re.split(_hard_md_pat, text, maxsplit=1, flags=re.MULTILINE)
        if len(parts) > 1:
            job_desc_and_position = parts[0]
            required_section = parts[1]
    elif re.search(_necessary_keywords, text):
        parts = re.split(_necessary_keywords, text, maxsplit=1)
        job_desc_and_position = parts[0]
        required_section = parts[1] if len(parts) > 1 else ""
        required_section = re.sub(r'^[\s:：（(]*.*?[）)]?[\s:：,，]*', '', required_section).strip()
    else:
        job_desc_and_position = text

    # 进一步分离职位描述和职位要求（使用正则匹配标题变体）
    _desc_pat = re.compile(_desc_keywords)
    _req_pat = re.compile(_req_keywords)
    _desc_m = _desc_pat.search(job_desc_and_position)
    _req_m = _req_pat.search(job_desc_and_position)

    if _desc_m and _req_m and _desc_m.start() < _req_m.start():
        # 职位描述在前，职位要求在后
        job_desc_section = job_desc_and_position[_desc_m.end():_req_m.start()]
        position_req_section = job_desc_and_position[_req_m.end():]
    elif _desc_m and _req_m and _req_m.start() < _desc_m.start():
        # 职位要求在前，职位描述在后
        position_req_section = job_desc_and_position[_req_m.end():_desc_m.start()]
        job_desc_section = job_desc_and_position[_desc_m.end():]
    elif _desc_m:
        job_desc_section = job_desc_and_position[_desc_m.end():]
    elif _req_m:
        position_req_section = job_desc_and_position[_req_m.end():]
    else:
        job_desc_section = job_desc_and_position

    # 如果是 markdown 格式，使用章节来分离
    if re.search(r'^#\s*', text, re.MULTILINE):
        _hard_md_full = rf'^##\s*{_hard_keywords}.*?(?=^##|\Z)'
        hard_match = re.search(_hard_md_full, text, flags=re.MULTILINE | re.DOTALL)
        work_exp_match = re.search(r'^###\s*工作经验.*?(?=^###|\Z)', text, flags=re.MULTILINE | re.DOTALL)
        edu_match = re.search(r'^###\s*学历要求.*?(?=^###|\Z)', text, flags=re.MULTILINE | re.DOTALL)

        sections = []
        if hard_match:
            sections.append(hard_match.group(0))
        if work_exp_match:
            sections.append(work_exp_match.group(0))
        if edu_match:
            sections.append(edu_match.group(0))
        required_section = '\n'.join(sections)

        # 提取软性条件章节（支持多种标题）
        _soft_md_full = rf'^##\s*{_soft_keywords}.*?(?=^##|\Z)'
        match = re.search(_soft_md_full, text, flags=re.MULTILINE | re.DOTALL)
        if match:
            position_req_section = match.group(0)
        job_desc_section = text

    # 行级结构化结果反向补强章节切分：兼容无标准标题、编号列表、单行条件混排。
    hard_lines = [row["text"] for row in structured_rows if row["kind"] == "hard"]
    preferred_lines_structured = [row["text"] for row in structured_rows if row["kind"] == "preferred"]
    skill_lines = [row["text"] for row in structured_rows if row["kind"] == "skill"]
    if hard_lines:
        required_section = (required_section + "\n" + "\n".join(hard_lines)).strip()
    if preferred_lines_structured or skill_lines:
        position_req_section = (
            position_req_section + "\n" + "\n".join(skill_lines + preferred_lines_structured)
        ).strip()

    # === 3. 提取经验要求 ===
    exp_value = 0

    # 中文数字 → 阿拉伯数字映射（来自 constants.CHINESE_NUMERALS）
    _num = r'(?:\d+|[零一二两三四五六七八九十])'

    def _to_int(s: str) -> int:
        """将阿拉伯数字或中文数字转为 int"""
        if s in CHINESE_NUMERALS:
            return CHINESE_NUMERALS[s]
        try:
            return int(s)
        except ValueError:
            return 0

    # 优先从明确的"工作年限"字段匹配
    work_exp_patterns = [
        rf'工作年限\s*[：:]\s*({_num})\s*[-~～]\s*({_num})\s*年',
        rf'工作年限\s*[：:]\s*({_num})\s*年(?:及以上|以上)?',
        rf'\*\*工作年限\*\*\s*[：:]\s*({_num})\s*[-~～]\s*({_num})\s*年',
        rf'\*\*工作年限\*\*\s*[：:]\s*({_num})\s*年(?:及以上|以上)?',
    ]

    for pattern in work_exp_patterns:
        match = re.search(pattern, required_section)
        if match:
            groups = match.groups()
            if len(groups) == 2 and groups[1] is not None:
                exp_value = _to_int(groups[0])
            else:
                exp_value = _to_int(groups[0])
            break

    # 如果没有找到，再用通用模式匹配
    if exp_value == 0:
        # 按优先级排序：越具体的模式越优先
        exp_patterns = [
            # X年以上工作经验 / 具有X年以上相关经验
            (rf'({_num})\s*年以上(?:\S*经验)?', lambda m: _to_int(m)),
            (rf'({_num})\s*年及以上', lambda m: _to_int(m)),
            # 至少X年 / 不低于X年 / 不少于X年（支持中文数字）
            (rf'(?:至少|不低于|不少于)\s*({_num})\s*年', lambda m: _to_int(m)),
            (rf'({_num})\s*年工作经验', lambda m: _to_int(m)),
            (rf'({_num})\s*年经验', lambda m: _to_int(m)),
            # X-Y 年 - 取最小值
            (rf'({_num})\s*[-~～]\s*({_num})\s*年', lambda m: min(_to_int(m[0]), _to_int(m[1]))),
            # 单独 X 年 - 最后使用（避免匹配"3-5年"中的"5年"）
            (rf'(?<!\d)(?<![一二两三四五六七八九十])({_num})\s*年(?!(?:以上|及|经验))', lambda m: _to_int(m)),
        ]

        # 优先从必要条件部分查找
        for pattern, extractor in exp_patterns:
            match = re.search(pattern, required_section)
            if match:
                if len(match.groups()) == 1:
                    exp_value = extractor(match.group(1))
                else:
                    exp_value = extractor(match.groups())
                break

    # 如果必要条件没有，从职位要求部分查找
    if exp_value == 0:
        for pattern, extractor in exp_patterns:
            match = re.search(pattern, position_req_section)
            if match:
                if len(match.groups()) == 1:
                    exp_value = extractor(match.group(1))
                else:
                    exp_value = extractor(match.groups())
                break

    # 最后从职位描述部分查找
    if exp_value == 0:
        for pattern, extractor in exp_patterns:
            match = re.search(pattern, job_desc_section)
            if match:
                if len(match.groups()) == 1:
                    exp_value = extractor(match.group(1))
                else:
                    exp_value = extractor(match.groups())
                break

    # === 4. 提取学历要求 ===
    edu_value = "不限"

    # 优先从明确的学历字段匹配（如"最低学历：本科"）
    edu_field_patterns = [
        r'最低学历\s*[：:]\s*([^\n]+)',
        r'学历\s*要求\s*[：:]\s*([^\n]+)',
        r'学历要求\s*[：:]\s*([^\n]+)',
        # 支持 markdown 粗体格式：**最低学历**：本科
        r'\*\*最低学历\*\*\s*[：:]\s*([^\n]+)',
        r'\*\*学历\*\*\s*[：:]\s*([^\n]+)',
    ]

    found_edu = None

    # 优先从必要条件部分查找明确的学历字段
    for pattern in edu_field_patterns:
        match = re.search(pattern, required_section)
        if match:
            edu_text = match.group(1).strip()
            # 从匹配结果中提取学历
            if '本科' in edu_text or '学士' in edu_text:
                found_edu = '本科'
                break
            elif '硕士' in edu_text or '研究生' in edu_text:
                found_edu = '硕士'
                break
            elif '博士' in edu_text or '博士后' in edu_text:
                found_edu = '博士'
                break
            elif '大专' in edu_text:
                found_edu = '大专'
                break
            elif '高中' in edu_text or '中专' in edu_text:
                found_edu = '高中'
                break
            elif '不限' in edu_text or '无要求' in edu_text:
                found_edu = '不限'
                break

    # 学历加分/偏好语境排除（防止"博士优先"被当作最低学历门槛）
    # 策略：先用正则精确移除偏好短语，再按句过滤整句偏好，最后兜底正则
    _edu_bonus_patterns = [
        r'博士优先', r'博士后优先', r'硕士优先', r'研究生优先',
        r'博士学历加分', r'博士学历优先', r'硕士学历优先',
        r'有博士学历', r'有博士', r'有硕士', r'有研究生',
        r'博士及以上优先', r'硕士及以上优先',
        r'博士学历者', r'硕士学历者',
        r'(?:本科|学士|硕士|研究生|博士|博士后|大专)(?:及以上)?学历(?:者)?(?:优先|加分|更佳|优先考虑)',
    ]

    # 如果没有找到明确字段，从必要条件部分提取学历关键词
    # 注意：最低学历应从低到高判断，因为"最低"意味着门槛，不应被加分项误导
    if found_edu is None and required_section:
        # 用正则精确移除偏好短语（保留同句中的硬门槛，如"本科，博士优先"→"本科"）
        cleaned_section = required_section
        for pattern in _edu_bonus_patterns:
            cleaned_section = re.sub(pattern, '', cleaned_section)
        # 从低到高判断：大专 → 本科 → 硕士 → 博士
        if '大专' in cleaned_section:
            found_edu = '大专'
        elif '高中' in cleaned_section or '中专' in cleaned_section:
            found_edu = '高中'
        elif '本科' in cleaned_section or '学士' in cleaned_section:
            found_edu = '本科'
        elif '硕士' in cleaned_section or '研究生' in cleaned_section:
            found_edu = '硕士'
        elif '博士' in cleaned_section or '博士后' in cleaned_section:
            found_edu = '博士'

    # 如果没有找到，从职位要求部分查找
    if found_edu is None:
        for pattern in edu_field_patterns:
            match = re.search(pattern, position_req_section)
            if match:
                edu_text = match.group(1).strip()
                if '本科' in edu_text or '学士' in edu_text:
                    found_edu = '本科'
                    break
                elif '硕士' in edu_text or '研究生' in edu_text:
                    found_edu = '硕士'
                    break
                elif '博士' in edu_text or '博士后' in edu_text:
                    found_edu = '博士'
                    break
                elif '大专' in edu_text:
                    found_edu = '大专'
                    break
                elif '高中' in edu_text or '中专' in edu_text:
                    found_edu = '高中'
                    break

    # 最后从职位描述部分查找（优先级最低）
    if found_edu is None:
        # 排除加分语境后再判断：正则精确移除偏好短语，从低到高
        cleaned_desc = job_desc_section
        for pattern in _edu_bonus_patterns:
            cleaned_desc = re.sub(pattern, '', cleaned_desc)
        if '大专' in cleaned_desc:
            found_edu = '大专'
        elif '高中' in cleaned_desc or '中专' in cleaned_desc:
            found_edu = '高中'
        elif '本科' in cleaned_desc or '学士' in cleaned_desc:
            found_edu = '本科'
        elif '硕士' in cleaned_desc or '研究生' in cleaned_desc:
            found_edu = '硕士'
        elif '博士' in cleaned_desc or '博士后' in cleaned_desc:
            found_edu = '博士'

    if found_edu:
        edu_value = found_edu

    # === 5. 提取必要条件（硬性约束）===
    required_conditions = []
    hard_edu_text = _hard_education_text(required_section, structured_rows)

    # 学历相关要求
    if '双证齐全' in hard_edu_text:
        required_conditions.append('双证齐全')
    if '统招' in hard_edu_text:
        if '本科' in hard_edu_text:
            required_conditions.append('统招本科')
        else:
            required_conditions.append('统招')
    if '全日制' in hard_edu_text:
        required_conditions.append('全日制')
    if '第一学历' in hard_edu_text:
        required_conditions.append('第一学历本科')
    if '985' in hard_edu_text and '211' in hard_edu_text and OR_HINT_RE.search(hard_edu_text):
        required_conditions.append({"type": "or", "items": ["985 院校", "211 院校"], "category": "院校背景"})
    else:
        if '985' in hard_edu_text:
            required_conditions.append('985 院校')
        if '211' in hard_edu_text:
            required_conditions.append('211 院校')

    # 年龄限制（多种格式：35岁以下 / 不超过40岁 / 25-35岁 / ≤35岁 / 35周岁以内）
    max_age = None
    age_patterns = [
        # 年龄35岁 / 年龄 35 周岁 / 年龄 35 岁以下（岁/周岁 必须出现）
        r'年龄[^\d]{0,5}(\d+)\s*(?:岁|周岁)(?:\s*(?:以下|以内|及以下))?',
        # 不超过40岁 / 不超过 35 周岁
        r'不超过\s*(\d+)\s*(?:岁|周岁)',
        # X岁以下 / X周岁以内（无需"年龄"前缀）
        r'(\d+)\s*(?:岁|周岁)\s*(?:以下|以内|及以下)',
        # ≤35岁 / <=35岁
        r'[≤<]=?\s*(\d+)\s*(?:岁|周岁)',
        # 25-35岁 / 25~35周岁（取上限）
        r'\d+\s*[-~～]\s*(\d+)\s*(?:岁|周岁)(?:\s*(?:以下|以内|之间))?',
        # 年龄35以下 / 年龄 35 以内（岁/周岁 可省略，但必须有范围限定词）
        r'年龄[^\d]{0,5}(\d+)\s*(?=以下|以内|及以下)',
    ]
    age_search_text = required_section + "\n" + position_req_section
    for pat in age_patterns:
        age_match = re.search(pat, age_search_text)
        if age_match:
            max_age = int(age_match.group(1))
            break
    if max_age:
        required_conditions.append(f'年龄≤{max_age}岁')

    # 从必要条件部分提取技术关键词（硬约束）。单句内有 OR/AND 线索时按句子语义建条件。
    tech_condition_keywords = []
    for row in structured_rows:
        if row["kind"] != "hard":
            continue
        found_terms = _find_terms(row["text"], TECH_SKILLS)
        if not found_terms:
            continue
        if len(found_terms) >= 2 and (OR_HINT_RE.search(row["text"]) or AND_HINT_RE.search(row["text"])):
            cond = _make_group_condition(found_terms, row["text"], "技术硬性条件")
            if cond and cond not in required_conditions:
                required_conditions.append(cond)
        else:
            for keyword in found_terms:
                if not any(keyword.lower() == existing.lower() for existing in tech_condition_keywords):
                    tech_condition_keywords.append(keyword)

    # 专业资格证书（必要条件）：扫描必要条件和职位描述两段
    cert_search_text = required_section + '\n' + position_req_section
    for cert in CERT_KEYWORDS:
        if cert.lower() in cert_search_text.lower():
            required_conditions.append(cert)

    # 行业经验（必要条件）：检测必要条件段落中的行业领域词及其子类
    # 多个方向应作为 OR 过滤，避免把"债券、基金、期货、期权等"误解为全部必须满足。
    _industry_experience_triggers = ['行业经验', '从业经验', '领域经验', '行业背景', '行业从业', '行业相关']
    _industry_search_text = "\n".join(
        row["text"] for row in structured_rows
        if row["section"] == "hard" or row["kind"] == "hard"
    )

    # 只有当必要条件中出现了"行业经验"等触发词，才激活行业检测（避免误判）
    if any(trigger in _industry_search_text for trigger in _industry_experience_triggers):
        industry_required_items = []
        for main_kw, aliases in INDUSTRY_CATEGORIES.items():
            # 主词或任一别名在搜索文本中出现 → 添加主词为必要条件
            if any(alias in _industry_search_text for alias in aliases):
                industry_required_items.append(main_kw)
        if len(industry_required_items) == 1:
            if not any(industry_required_items[0] == existing for existing in required_conditions):
                required_conditions.append(industry_required_items[0])
        elif len(industry_required_items) > 1:
            required_conditions.append({
                "type": "or",
                "items": industry_required_items,
                "category": "金融投资行业经验"
            })

    for cond in _extract_special_experience_conditions(structured_rows):
        if cond not in required_conditions:
            required_conditions.append(cond)

    # === 6. 提取技能关键词（从所有部分）- 软性要求，用于评分 ===
    soft_skills = []

    # 合并所有部分作为技能提取源
    # markdown 格式中，技能可能在硬性条件（### 技能要求）或软性条件中
    combined_soft_section = required_section + "\n" + job_desc_section + "\n" + position_req_section

    soft_skills.extend(_find_terms(combined_soft_section, TECH_SKILLS))

    # 去重（忽略大小写，同时处理空格变体）
    unique_soft_skills = []
    seen_skills = set()
    for skill in soft_skills:
        skill_key = re.sub(r'\s+', '', skill).lower()
        if skill_key not in seen_skills:
            seen_skills.add(skill_key)
            unique_soft_skills.append(skill)

    # 返回解析结果
    salary_min, salary_max = _extract_salary_range(text)
    work_location = _extract_work_location(text)
    gender = _extract_gender_requirement(text)

    # 构建溯源数据：每个解析结果对应的原文片段
    _source_map: Dict = {}

    # 学历溯源
    if edu_value != "不限":
        _edu_search = required_section or position_req_section or job_desc_section or text
        _source_map["edu"] = _find_source_line(_edu_search, edu_value)

    # 经验溯源
    if exp_value > 0:
        _exp_pat = re.compile(rf'{exp_value}\s*年')
        _source_map["min_exp"] = _find_pattern_source_line(
            required_section or position_req_section or text, _exp_pat
        ) or _find_pattern_source_line(text, _exp_pat)

    # 薪资溯源
    if salary_min is not None or salary_max is not None:
        _salary_pat = re.compile(r'薪资|薪酬|待遇|月薪|年薪|底薪|工资')
        _source_map["salary"] = _find_pattern_source_line(text, _salary_pat)

    # 地点溯源
    if work_location:
        _source_map["work_location"] = _find_source_line(text, work_location)

    # 年龄溯源
    if max_age:
        _age_pat = re.compile(rf'{max_age}\s*(?:岁|周岁)')
        _source_map["max_age"] = _find_pattern_source_line(text, _age_pat)

    if gender != "不限":
        _gender_pat = re.compile(r'性别|仅限|只限|限招|只招')
        _source_map["gender"] = _find_pattern_source_line(text, _gender_pat)

    # 技能溯源：为每个技能找到原文出处
    _skills_source = []
    _combined_text = required_section + "\n" + job_desc_section + "\n" + position_req_section
    for skill in unique_soft_skills:
        evidence = _find_source_line(_combined_text, skill) or _find_source_line(text, skill)
        _skills_source.append({"name": skill, "evidence": evidence})
    _source_map["skills"] = _skills_source

    # 必要条件溯源
    _required_source = []
    for cond in required_conditions:
        evidence = ""
        if isinstance(cond, dict):
            # OR/AND 条件：用第一个 item 搜索，合成文本需提取关键词
            items = cond.get("items", [])
            if items:
                first_item = items[0]
                evidence = _find_source_line(text, first_item)
                # 合成文本（如"Java经验≥3年"）搜不到时，提取领域词重试
                if not evidence and "经验≥" in first_item:
                    domain = first_item.split("经验≥")[0]
                    evidence = _find_source_line(text, domain)
        elif isinstance(cond, str):
            # 合成条件（如"年龄≤35岁"）用关键词回搜原文
            if cond.startswith("年龄≤"):
                _age_val = cond.replace("年龄≤", "").replace("岁", "")
                evidence = _find_source_line(text, _age_val) if _age_val else ""
            elif "经验≥" in cond:
                # "Java经验≥3年" → 搜 "Java"
                _domain = cond.split("经验≥")[0]
                evidence = _find_source_line(text, _domain)
            else:
                evidence = _find_source_line(text, cond)
        _required_source.append({"condition": cond, "evidence": evidence})
    _source_map["required_conditions"] = _required_source

    # 保留原始段落供 generate_config_from_text 追踪 preferred_keywords
    _source_map["_raw_required_section"] = required_section
    _source_map["_raw_job_desc"] = job_desc_section
    _source_map["_raw_position_req"] = position_req_section

    return {
        "job_title": job_title,
        "min_exp": exp_value,
        "edu": edu_value,
        "gender": gender,
        "work_location": work_location,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "max_age": max_age,
        "soft_skills": unique_soft_skills,  # 职位描述中的技能要求（用于评分）
        "required_conditions": required_conditions,
        "tech_conditions": tech_condition_keywords,  # 必要条件中的技术要求（只需满足其一）
        "_source_map": _source_map,
    }


def generate_config_from_text(requirements_text: str, merge_existing: bool = True) -> Dict:
    """
    从招聘需求文本生成完整的配置文件（支持权重）
    merge_existing: True=合并到现有配置（岗位覆盖或新增），False=生成新配置
    """
    parsed = parse_job_requirements(requirements_text)

    # 对 soft_skills 去重（忽略大小写）
    skills = parsed["soft_skills"]
    seen = set()
    unique_skills = []
    for skill in skills:
        skill_lower = skill.lower()
        if skill_lower not in seen:
            seen.add(skill_lower)
            unique_skills.append(skill)

    # 为技能分配权重
    weighted_keywords = []
    preferred_keywords = []
    preferred_seen = set()

    def _is_preferred_line(line: str) -> bool:
        return bool(re.search(r'优先|加分|更佳|优先考虑|优先录用', line))

    def _add_preferred_keyword(name: str, bonus: int = 2) -> None:
        key = re.sub(r'\s+', '', name).lower()
        if key and key not in preferred_seen:
            preferred_keywords.append({"name": name, "bonus": bonus})
            preferred_seen.add(key)

    preferred_lines = [_preferred_clause_text(line) for line in requirements_text.split('\n') if _is_preferred_line(line)]
    preferred_lines = [line for line in preferred_lines if line]

    # 提取职位名称中的技术关键词（Java、Python 等），这些词权重调高
    job_title = parsed.get("job_title", "").lower()
    position_tech_keywords = []

    # 从职位名称中提取技术关键词
    tech_keywords_in_title = [
        'java', 'python', 'javascript', 'typescript', 'go', 'c++', 'c#', 'php', 'ruby', 'swift', 'kotlin', 'scala',
        '前端', '后端', '全栈', '移动端', 'android', 'ios',
        'ai', '算法', '数据', '测试', '运维', '开发', '分析',
        '固收', '量化', '金融', '证券', '风控',
    ]

    for kw in tech_keywords_in_title:
        if kw in job_title:
            position_tech_keywords.append(kw.lower())

    # 兜底的 AI 关键词列表（当需求没有明确说明时使用）
    default_ai_keywords = ['llm', '大模型', 'ai agent', '智能体', 'langchain', '智能问答', '知识库', 'rag', '向量数据库', '生成式 ai', 'aigc', 'spring ai']

    for skill in unique_skills:
        weight = 1  # 默认权重
        skill_lower = skill.lower()
        matching_lines = [line for line in requirements_text.split('\n') if _term_in_text(skill, line)]
        preferred_matching_lines = [line for line in preferred_lines if _term_in_text(skill, line)]
        preferred_only = bool(matching_lines) and len(preferred_matching_lines) == len(matching_lines)
        if preferred_only:
            if skill_lower in {'agent', 'ai agent', '大模型'} and any(re.search(r'agent', line, re.IGNORECASE) and '大模型' in line for line in preferred_matching_lines):
                continue
            _add_preferred_keyword(skill, 2)
            continue

        # 检查技能在原文中的上下文，确定权重
        # 权重 3：精通、擅长、深入、核心
        # 权重 2：熟练、熟悉、掌握、有...经验、职位名称相关
        # 权重 1：了解、接触过、基本
        # 注意：中文和英文之间可能有空格，需要用正则匹配

        if re.search(rf'{re.escape(skill_lower)}\s*精通|精通\s*{re.escape(skill_lower)}', requirements_text.lower()):
            weight = 3
        elif re.search(rf'{re.escape(skill_lower)}\s*熟练|熟练\s*{re.escape(skill_lower)}|'
                       rf'{re.escape(skill_lower)}\s*熟悉|熟悉\s*{re.escape(skill_lower)}|'
                       rf'{re.escape(skill_lower)}\s*深入|深入\s*{re.escape(skill_lower)}', requirements_text.lower()):
            weight = 2
        # 检查是否在"优先/熟悉/熟练"条件所在的同一行（支持同一行中多个技能共享权重）
        for line in requirements_text.split('\n'):
            line_lower = line.lower()
            if _term_in_text(skill, line):
                # 检查该行是否有技能熟练度关键词；"优先"单独进入 preferred_keywords
                if re.search(r'精通|擅长|深入|核心', line_lower):
                    weight = 3
                    break
                if re.search(r'熟悉|熟练|掌握', line_lower):
                    weight = max(weight, 2)
                    break

        # 如果技能与职位名称相关，权重设为 2
        if weight == 1 and position_tech_keywords:
            for pos_kw in position_tech_keywords:
                if pos_kw in skill_lower or skill_lower in pos_kw:
                    weight = 2
                    break

        # AI 关键词权重处理：无明确优先语境时，AI 相关技能默认略高权重。
        if not preferred_lines:
            if any(ai_kw in skill_lower for ai_kw in default_ai_keywords):
                weight = 2

        weighted_keywords.append({"name": skill, "weight": weight})

    # === 提取"优先"类行业/领域加分关键词 ===
    # 从"X经验者优先"、"X行业优先"等表述中提取领域词，作为额外加分（非硬过滤）
    for line in preferred_lines:
        if '985' in line:
            _add_preferred_keyword('985 院校', 2)
        if '211' in line:
            _add_preferred_keyword('211 院校', 2)
        if '双一流' in line:
            _add_preferred_keyword('双一流院校', 2)
        if re.search(r'硕士|研究生', line):
            _add_preferred_keyword('硕士', 2)
        if re.search(r'博士|博士后', line):
            _add_preferred_keyword('博士', 2)
        for bk in BONUS_DOMAIN_KEYWORDS:
            if bk in line.lower():
                _add_preferred_keyword(bk, 2)
        for match in re.finditer(r'(?:有|具备)?([\u4e00-\u9fa5A-Za-z0-9+#/. ]{2,24}?)(?:经验|背景|经历|能力)(?:者)?优先', line):
            preferred_name = _normalize_preferred_name(match.group(1))
            if preferred_name:
                _add_preferred_keyword(preferred_name, 2)

    # 合并必要条件和技术条件。技术硬条件进入 required_conditions 的 OR 条件，
    # 避免同时写入旧 tech_conditions 字段导致命令行筛选路径重复检查。
    all_required = parsed["required_conditions"].copy()
    tech_conditions = parsed.get("tech_conditions", [])
    if tech_conditions:
        all_required.append({"type": "or", "items": tech_conditions, "category": "技术硬性条件"})

    # 生成新岗位配置
    new_job_config = {
        "min_exp": parsed["min_exp"],
        "edu": parsed["edu"],
        "gender": parsed.get("gender", "不限"),
        "work_location": parsed.get("work_location", ""),
        "salary_min": parsed.get("salary_min"),
        "salary_max": parsed.get("salary_max"),
        "max_age": parsed.get("max_age"),
        "keywords": weighted_keywords,  # 带权重的技能列表
        "preferred_keywords": preferred_keywords,  # 优先项：额外加分，不参与关键词分母
        "required_conditions": all_required,
    }

    # 如果合并现有配置，则读取并更新
    if merge_existing:
        config_file = "job_config.json"
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    existing_config = json.load(f)
            except Exception:
                existing_config = {"job_requirements": {}}
        else:
            existing_config = {"job_requirements": {}}

        # 确保 job_requirements 键存在
        if "job_requirements" not in existing_config:
            existing_config["job_requirements"] = {}
    else:
        existing_config = {"job_requirements": {}}

    # 更新或新增岗位配置
    job_title = parsed["job_title"]
    if job_title == "default":
        logger.warning("'default' 是保留字，不能作为岗位名称")
        job_title = "Java 工程师"  # 使用默认名称

    # 规范化岗位名称：去除多余空格，统一格式
    normalized_job_title = _clean_job_title(job_title)

    # 检查是否已存在相同（规范化后）的岗位，如果存在则覆盖
    existing_key_to_delete = None
    for key in existing_config["job_requirements"].keys():
        normalized_key = re.sub(r'\s+', ' ', key).strip()
        if normalized_key.lower() == normalized_job_title.lower():
            existing_key_to_delete = key
            break

    if existing_key_to_delete:
        logger.info("检测到重复岗位：'%s'，将进行覆盖更新", existing_key_to_delete)
        del existing_config["job_requirements"][existing_key_to_delete]

    existing_config["job_requirements"][normalized_job_title] = new_job_config

    # 传递溯源数据供 GUI 展示
    source_map = parsed.get("_source_map")
    if source_map:
        # 补充 preferred_keywords 的溯源（parse_job_requirements 不追踪 preferred）
        _skills_source = source_map.get("skills", [])
        _existing_names = {re.sub(r'\s+', '', s["name"]).lower() for s in _skills_source}
        _combined_text = (parsed.get("_source_map", {}).get("_raw_required_section", "")
                          + "\n" + parsed.get("_source_map", {}).get("_raw_job_desc", "")
                          + "\n" + parsed.get("_source_map", {}).get("_raw_position_req", ""))
        if not _combined_text.strip():
            _combined_text = requirements_text
        for pref in preferred_keywords:
            name = pref.get("name", "") if isinstance(pref, dict) else str(pref)
            key = re.sub(r'\s+', '', name).lower()
            if key not in _existing_names:
                evidence = _find_source_line(_combined_text, name) or _find_source_line(requirements_text, name)
                _skills_source.append({"name": name, "evidence": evidence})
        source_map["skills"] = _skills_source
        existing_config["_source_map"] = source_map

    return existing_config


def save_config(config: Dict, filename: str = "job_config.json"):
    """保存配置到文件"""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=4)
    print(f"配置已保存到 {filename}")


def main():
    """主函数"""
    print("招聘需求文档解析器")
    print("=" * 50)
    print("请粘贴您的招聘需求文档内容，结束后输入 'END'：")
    print()

    lines = []
    while True:
        try:
            line = input()
            if line.strip().upper() == 'END':
                break
            lines.append(line)
        except KeyboardInterrupt:
            print("\n操作已取消")
            return

    requirements_text = '\n'.join(lines)

    if not requirements_text.strip():
        print("未输入任何内容")
        return

    print("\n正在解析招聘需求...")
    try:
        # 检查是否已存在该岗位
        parsed = parse_job_requirements(requirements_text)
        job_title = parsed["job_title"]

        config_file = "job_config.json"
        is_new_job = True
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
                    # default 不是岗位，跳过
                    job_keys = [k for k in existing.get("job_requirements", {}).keys() if k != "default"]
                    if job_title in job_keys:
                        is_new_job = False
            except Exception:
                pass

        if is_new_job:
            print(f"\n[新增岗位] {job_title}")
        else:
            print(f"\n[覆盖岗位] {job_title}")

        config = generate_config_from_text(requirements_text)
        job_info = config["job_requirements"][job_title]

        print("\n解析结果：")
        print(f"职位名称：{job_title}")
        print(f"最低经验：{job_info['min_exp']}年")
        print(f"最低学历：{job_info['edu']}")
        print(f"技能要求（软性，用于评分）：{[k.get('name', k) if isinstance(k, dict) else k for k in job_info['keywords']]}")
        print(f"必要条件（硬性）：{job_info.get('required_conditions', [])}")
        print(f"技术条件（OR 检查）：{job_info.get('tech_conditions', [])}")

        save_config(config)

        print("\n提示：配置文件已生成，运行 bossmaster.py 时将自动使用")

    except Exception as e:
        print(f"解析出错：{e}")
        import traceback
        print(traceback.format_exc())


if __name__ == "__main__":
    main()
