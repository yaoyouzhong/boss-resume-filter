import threading
from unittest.mock import patch

import requests

import resume_ai_profile
from resume_ai_profile import (
    build_profile_messages,
    extract_profile_with_ai,
    merge_profile,
    normalize_ai_profile,
    parse_profile_payload,
)


API_CONFIG = {
    "api_provider": "OpenAI",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",
}


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_parse_profile_payload_plain_json():
    payload = parse_profile_payload('{"salary": "10-15K", "age": 33}')
    assert payload == {"salary": "10-15K", "age": 33}


def test_parse_profile_payload_fenced():
    payload = parse_profile_payload('```json\n{"gender": "男"}\n```')
    assert payload == {"gender": "男"}


def test_parse_profile_payload_chinese_punctuation_repaired():
    payload = parse_profile_payload('{“salary”：“面议”}')
    assert payload == {"salary": "面议"}


def test_parse_profile_payload_non_dict_raises():
    for bad in ('["salary"]', "123"):
        try:
            parse_profile_payload(bad)
        except ValueError:
            continue
        raise AssertionError(f"非对象 JSON 应抛 ValueError: {bad}")


def test_parse_profile_payload_garbage_raises():
    for bad in ("", "  ", "这不是 JSON", "抱歉，我无法提取"):
        try:
            parse_profile_payload(bad)
        except ValueError:
            continue
        raise AssertionError(f"垃圾输入应抛 ValueError: {bad!r}")


def test_normalize_salary_forms():
    assert normalize_ai_profile({"salary": "15-25K"})["salary"] == "15-25K"
    assert normalize_ai_profile({"salary": "12.5K"})["salary"] == "12.5K"
    assert normalize_ai_profile({"salary": "15~25k"})["salary"] == "15-25K"
    assert normalize_ai_profile({"salary": "面议"})["salary"] == "面议"
    # 左端大于右端区间丢弃；非 K 形态丢弃
    assert "salary" not in normalize_ai_profile({"salary": "25-15K"})
    assert "salary" not in normalize_ai_profile({"salary": "15000元/月"})
    assert "salary" not in normalize_ai_profile({"salary": "15000"})


def test_normalize_age_and_exp_years_bounds():
    assert normalize_ai_profile({"age": 33})["age"] == "33"
    assert normalize_ai_profile({"age": "30岁"})["age"] == "30"
    assert "age" not in normalize_ai_profile({"age": 12})
    assert "age" not in normalize_ai_profile({"age": 99})
    assert "age" not in normalize_ai_profile({"age": "未知"})
    assert normalize_ai_profile({"exp_years": "6年"})["exp_years"] == "6"
    assert normalize_ai_profile({"exp_years": 0})["exp_years"] == "0"
    assert "exp_years" not in normalize_ai_profile({"exp_years": 60})


def test_normalize_gender_education_job_status():
    assert normalize_ai_profile({"gender": "男"})["gender"] == "男"
    assert "gender" not in normalize_ai_profile({"gender": "未知"})
    assert normalize_ai_profile({"education": "专科"})["education"] == "大专"
    assert normalize_ai_profile({"education": "本科"})["education"] == "本科"
    assert normalize_ai_profile({"education": "本科学历"})["education"] == "本科"
    assert "education" not in normalize_ai_profile({"education": "初中"})
    # 求职状态收紧枚举：含"不考虑"才映射暂不考虑，其余宽松说法归类，未知丢弃
    assert normalize_ai_profile({"job_status": "已离职，随时到岗"})["job_status"] == "离职"
    assert normalize_ai_profile({"job_status": "在职-考虑机会"})["job_status"] == "在职"
    assert normalize_ai_profile({"job_status": "在职-暂不考虑机会"})["job_status"] == "暂不考虑"
    assert "job_status" not in normalize_ai_profile({"job_status": "观望中"})


def test_normalize_text_fields():
    assert normalize_ai_profile({"city": "上海"})["city"] == "上海"
    assert normalize_ai_profile({"company": " 凯捷 咨询 "})["company"] == "凯捷咨询"
    assert normalize_ai_profile({"school": "清华大学"})["school"] == "清华大学"
    # 纯英文、超长一律丢弃
    assert "city" not in normalize_ai_profile({"city": "Shanghai"})
    assert "company" not in normalize_ai_profile({"company": "公" * 41})


def test_merge_profile_fills_blanks():
    merged = merge_profile(
        {"salary": "", "age": "", "education": "本科"},
        {"salary": "10-15K", "age": "45", "education": "本科"},
    )
    assert merged["info"]["salary"] == "10-15K"
    assert merged["info"]["age"] == "45"
    assert [item["field"] for item in merged["filled"]] == ["salary", "age"]
    assert merged["conflicts"] == []


def test_merge_profile_conflict_keeps_regex_value():
    merged = merge_profile({"age": "28"}, {"age": "45"})
    assert merged["info"]["age"] == "28"
    assert merged["filled"] == []
    assert merged["conflicts"] == [
        {"field": "age", "label": "年龄", "rule": "28", "ai": "45"}
    ]


def test_merge_profile_equivalent_values_are_not_conflicts():
    merged = merge_profile(
        {"city": "上海市", "age": "30", "salary": "15-25K"},
        {"city": "上海", "age": 30, "salary": "15-25k"},
    )
    assert merged["conflicts"] == []
    assert merged["filled"] == []


def test_extract_profile_with_ai_success():
    regex_info = {"salary": "", "age": "", "education": "大专"}
    response = (
        '{"salary": "10-15K", "age": 45, "education": "本科", "city": "南京"}'
    )
    with patch.object(resume_ai_profile, "_call_profile_chat", return_value=response):
        result = extract_profile_with_ai("简历全文", regex_info, API_CONFIG, "key")
    assert result["error"] == ""
    assert result["info"]["salary"] == "10-15K"
    assert result["info"]["age"] == "45"
    assert result["info"]["city"] == "南京"
    assert result["info"]["education"] == "大专"
    assert [item["field"] for item in result["filled"]] == ["salary", "age", "city"]
    assert result["conflicts"] == [
        {"field": "education", "label": "学历", "rule": "大专", "ai": "本科"}
    ]


def test_extract_profile_with_ai_http_401_returns_error():
    with patch.object(
        resume_ai_profile.requests, "post", return_value=_FakeResponse(401)
    ):
        result = extract_profile_with_ai("简历全文", {"salary": ""}, API_CONFIG, "bad-key")
    assert result["info"] == {"salary": ""}
    assert result["filled"] == []
    assert "鉴权失败" in result["error"]


def test_extract_profile_with_ai_timeout_returns_error():
    with patch.object(
        resume_ai_profile.requests,
        "post",
        side_effect=requests.exceptions.ReadTimeout(),
    ), patch.object(resume_ai_profile.time, "sleep"):
        result = extract_profile_with_ai("简历全文", {}, API_CONFIG, "key")
    assert "读取超时" in result["error"]
    assert result["info"] == {}


def test_extract_profile_with_ai_stop_event_returns_none():
    stop_event = threading.Event()
    stop_event.set()
    with patch.object(
        resume_ai_profile, "_call_profile_chat", side_effect=AssertionError("不应调用 AI")
    ):
        assert extract_profile_with_ai(
            "简历全文", {}, API_CONFIG, "key", stop_event=stop_event
        ) is None


def test_extract_profile_with_ai_invalid_json_returns_error():
    with patch.object(resume_ai_profile, "_call_profile_chat", return_value="无法理解"):
        result = extract_profile_with_ai("简历全文", {"age": "28"}, API_CONFIG, "key")
    assert result["error"]
    assert result["info"] == {"age": "28"}
    assert result["filled"] == []


def test_build_profile_messages_does_not_leak_regex_draft():
    messages = build_profile_messages("张三的简历全文")
    assert messages[0]["role"] == "system"
    assert "禁止推测" in messages[0]["content"]
    assert "简历全文" in messages[1]["content"]


def test_build_profile_messages_company_field_excludes_project_clients():
    """乙方简历只有项目经历：项目客户名（如"华泰证券"）不得当作任职公司。"""
    messages = build_profile_messages("简历")
    company_rule = next(
        line for line in messages[1]["content"].splitlines() if '"company"' in line
    )
    assert "项目" in company_rule
    assert "空字符串" in company_rule
