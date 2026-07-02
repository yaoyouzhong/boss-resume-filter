"""Unit tests for llm_eval module — mocked HTTP, no real API calls."""
import contextlib
import io
import json
import threading
from unittest.mock import patch, MagicMock

from llm_eval import (
    LLMEvalResult,
    build_llm_candidate_summary,
    _build_prompt,
    _parse_response,
    _call_llm_api,
    _extract_evaluation_payload,
    _format_ai_log_summary,
    _recalc_recommend_level,
    _use_forced_function_output,
    evaluate_batch,
    evaluate_with_resume,
    _resolve_eval_workers,
    _resolve_request_timeout,
)


def quiet_evaluate_batch(*args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return evaluate_batch(*args, **kwargs)


# === _parse_response ===

def test_parse_response_plain_json():
    result = _parse_response('{"adjustment": 5, "reason": "匹配良好"}')
    assert result['adjustment'] == 5
    assert result['reason'] == "匹配良好"


def test_parse_response_json_in_markdown_block():
    text = '```json\n{"adjustment": -3, "reason": "经验不足"}\n```'
    result = _parse_response(text)
    assert result['adjustment'] == -3


def test_parse_response_json_with_surrounding_text():
    text = '根据分析，结果如下：\n{"adjustment": 0, "reason": "基本匹配"}\n请参考。'
    result = _parse_response(text)
    assert result['adjustment'] == 0


def test_parse_response_chinese_punctuation_normalized():
    text = '{"adjustment"：7，"reason"："高度匹配"}'
    result = _parse_response(text)
    assert result['adjustment'] == 7


def test_parse_response_repairs_missing_commas_between_lines():
    text = """{
      "adjustment": -3
      "hard_condition_verdict": "unknown"
      "hard_condition_findings": []
      "reason": "存在部分风险"
    }"""
    result = _parse_response(text)
    assert result['adjustment'] == -3
    assert result['hard_condition_verdict'] == "unknown"
    assert result['reason'] == "存在部分风险"


def test_parse_response_adjustment_clamped_high():
    result = _parse_response('{"adjustment": 99, "reason": "极好"}')
    assert result['adjustment'] == 15


def test_parse_response_adjustment_clamped_low():
    result = _parse_response('{"adjustment": -50, "reason": "极差"}')
    assert result['adjustment'] == -15


def test_parse_response_float_adjustment_to_int():
    result = _parse_response('{"adjustment": 3.7, "reason": "不错"}')
    assert result['adjustment'] == 3
    assert isinstance(result['adjustment'], int)


def test_parse_response_missing_adjustment_defaults_zero():
    result = _parse_response('{"reason": "没有分数"}')
    assert result['adjustment'] == 0


def test_parse_response_empty_raises():
    raised = False
    try:
        _parse_response('')
    except ValueError:
        raised = True
    assert raised


def test_parse_response_unparseable_raises():
    raised = False
    try:
        _parse_response('这不是 JSON')
    except ValueError:
        raised = True
    assert raised


def test_parse_response_reason_truncated():
    long_reason = "A" * 300
    result = _parse_response(json.dumps({"adjustment": 0, "reason": long_reason}))
    assert len(result['reason']) == 200


def test_format_ai_log_summary_is_single_line_and_truncated():
    summary = _format_ai_log_summary(
        {"qualification_status": "qualified"},
        "技能匹配良好。\n" + "补充说明" * 30,
        70,
    )
    assert summary.startswith("通过：技能匹配良好。")
    assert "\n" not in summary
    assert summary.endswith("…")
    assert len(summary) <= 84


def test_format_ai_log_summary_uses_business_conclusion():
    assert _format_ai_log_summary(
        {"qualification_status": "manual_review"}, "学历形式缺少证据", 64
    ).startswith("待确认：")
    assert _format_ai_log_summary(
        {"qualification_status": "rejected"}, "工作经验不足", 80
    ).startswith("硬条件淘汰：")
    assert _format_ai_log_summary(
        {"qualification_status": "qualified"}, "评分不足", 54
    ).startswith("评分淘汰：")


# === _build_prompt ===

def test_build_prompt_returns_system_and_user():
    msgs = _build_prompt("岗位要求：Java 5年", "张三，5年Java经验")
    assert len(msgs) == 2
    assert msgs[0]['role'] == 'system'
    assert msgs[1]['role'] == 'user'
    assert "岗位要求：Java 5年" in msgs[1]['content']
    assert "张三，5年Java经验" in msgs[1]['content']


def test_qwen37_dashscope_uses_forced_function_output():
    assert _use_forced_function_output({
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.7-max",
    }) is True
    assert _use_forced_function_output({
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1",
    }) is False


def test_extract_evaluation_payload_prefers_tool_arguments():
    arguments = '{"adjustment": 1, "reason": "ok"}'
    message = {
        "content": "",
        "tool_calls": [{
            "function": {
                "name": "submit_candidate_evaluation",
                "arguments": arguments,
            }
        }],
    }
    assert _extract_evaluation_payload(message, True) == arguments


def test_build_llm_candidate_summary_compacts_api_resume_sections():
    long_responsibility = "负责 Python 数据分析、ETL 调度、Oracle 数据库开发。" * 40
    candidate = {
        "name": "张三",
        "match_score": 68,
        "recommend_level": "推荐",
        "skill_match_ratio": "3/5",
        "skill_matches": ["Python", "ETL", "Oracle"],
        "risk_flags": ["学历形式待确认：疑似非统招本科"],
        "summary": "\n".join([
            "张三，8年经验，期望北京",
            "教育经历：南京大学 计算机科学 本科 2008 2012",
            "工作经历：某证券公司 数据分析师 2020 至今",
            f"工作职责：{long_responsibility}",
            "技能标签：Python、ETL、Oracle、Python",
            "完整无关原文：" + "A" * 3000,
        ]),
        "score_explanation": ["技能分：30/50", "学历：本科等级通过，学历形式待人工确认"],
    }

    compact = build_llm_candidate_summary(candidate, max_chars=1350)

    assert len(compact) <= 1353
    assert "规则评分：68" in compact
    assert "风险提示：学历形式待确认：疑似非统招本科" in compact
    assert "教育经历：南京大学 计算机科学 本科 2008 2012" in compact
    assert "工作职责：负责 Python 数据分析" in compact
    assert "技能标签：Python、ETL、Oracle" in compact
    assert "A" * 1000 not in compact


# === _recalc_recommend_level ===

def test_recalc_strong_recommend():
    assert _recalc_recommend_level(75) == "强烈推荐"
    assert _recalc_recommend_level(100) == "强烈推荐"


def test_recalc_recommend():
    assert _recalc_recommend_level(65) == "推荐"
    assert _recalc_recommend_level(74) == "推荐"


def test_recalc_pending():
    assert _recalc_recommend_level(55) == "待定"
    assert _recalc_recommend_level(64) == "待定"
    assert _recalc_recommend_level(0) == "已淘汰"


# === _call_llm_api (mocked HTTP) ===

@patch('llm_eval.requests')
def test_call_llm_api_success(mock_requests):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        'choices': [{'message': {'content': '{"adjustment": 5, "reason": "匹配"}'}}]
    }
    mock_session = MagicMock()
    mock_session.post.return_value = mock_response
    mock_requests.Session.return_value = mock_session

    api_config = {'base_url': 'https://api.example.com/v1', 'model': 'test-model'}
    messages = [{"role": "user", "content": "test"}]
    result = _call_llm_api(messages, api_config, "fake-key")
    assert result.success is True
    assert result.adjustment == 5
    assert result.model == 'test-model'


@patch('llm_eval.requests')
def test_call_llm_api_qwen37_uses_function_arguments(mock_requests):
    import requests as real_requests
    mock_requests.exceptions = real_requests.exceptions
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        'choices': [{
            'finish_reason': 'stop',
            'message': {
                'content': '',
                'tool_calls': [{
                    'function': {
                        'name': 'submit_candidate_evaluation',
                        'arguments': json.dumps({
                            'adjustment': 3,
                            'reason': '匹配',
                            'hard_condition_verdict': 'unknown',
                            'hard_condition_findings': [],
                        }, ensure_ascii=False),
                    }
                }],
            },
        }]
    }
    mock_session = MagicMock()
    mock_session.post.return_value = mock_response
    mock_requests.Session.return_value = mock_session

    api_config = {
        'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'model': 'qwen3.7-max',
    }
    result = _call_llm_api([{"role": "user", "content": "test"}], api_config, "fake-key")

    assert result.success is True
    assert result.adjustment == 3
    request_body = mock_session.post.call_args.kwargs["json"]
    assert request_body["enable_thinking"] is False
    assert request_body["temperature"] == 0
    assert request_body["tool_choice"]["function"]["name"] == "submit_candidate_evaluation"


@patch('llm_eval.requests')
def test_call_llm_api_retries_once_after_invalid_json(mock_requests):
    import requests as real_requests
    mock_requests.exceptions = real_requests.exceptions
    invalid_response = MagicMock()
    invalid_response.status_code = 200
    invalid_response.json.return_value = {
        'choices': [{'message': {'content': '{"adjustment": 5 "reason": "缺少逗号"}'}}]
    }
    valid_response = MagicMock()
    valid_response.status_code = 200
    valid_response.json.return_value = {
        'choices': [{'message': {'content': '{"adjustment": 5, "reason": "匹配"}'}}]
    }
    mock_session = MagicMock()
    mock_session.post.side_effect = [invalid_response, valid_response]
    mock_requests.Session.return_value = mock_session

    api_config = {'base_url': 'https://api.example.com/v1', 'model': 'test-model'}
    with contextlib.redirect_stdout(io.StringIO()):
        result = _call_llm_api([{"role": "user", "content": "test"}], api_config, "fake-key")

    assert result.success is True
    assert result.adjustment == 5
    assert mock_session.post.call_count == 2
    retry_body = mock_session.post.call_args_list[1].kwargs["json"]
    assert retry_body["temperature"] == 0
    assert "上次返回的 JSON 格式有误" in retry_body["messages"][-1]["content"]


@patch('llm_eval.requests')
def test_call_llm_api_stops_after_one_json_format_retry(mock_requests):
    import requests as real_requests
    mock_requests.exceptions = real_requests.exceptions
    invalid_response = MagicMock()
    invalid_response.status_code = 200
    invalid_response.json.return_value = {
        'choices': [{'message': {'content': '{"adjustment": 5 "reason": "仍然错误"}'}}]
    }
    mock_session = MagicMock()
    mock_session.post.side_effect = [invalid_response, invalid_response]
    mock_requests.Session.return_value = mock_session

    api_config = {'base_url': 'https://api.example.com/v1', 'model': 'test-model'}
    with contextlib.redirect_stdout(io.StringIO()):
        result = _call_llm_api([{"role": "user", "content": "test"}], api_config, "fake-key")

    assert result.success is False
    assert result.reason.startswith("AI 返回格式错误（自动纠正后仍无法解析；")
    assert "返回长度" in result.reason
    assert mock_session.post.call_count == 2


@patch('llm_eval.requests')
def test_call_llm_api_client_error_no_retry(mock_requests):
    import contextlib, io
    import requests as real_requests
    mock_requests.exceptions = real_requests.exceptions
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"
    mock_session = MagicMock()
    mock_session.post.return_value = mock_response
    mock_requests.Session.return_value = mock_session

    api_config = {'base_url': 'https://api.example.com/v1', 'model': 'test-model'}
    with contextlib.redirect_stdout(io.StringIO()):
        result = _call_llm_api([{"role": "user", "content": "test"}], api_config, "bad-key")
    assert result.success is False
    assert mock_session.post.call_count == 1


@patch('llm_eval.time.sleep')
@patch('llm_eval.requests')
def test_call_llm_api_timeout_retries(mock_requests, mock_sleep):
    import contextlib, io
    import requests as real_requests
    mock_requests.exceptions = real_requests.exceptions
    mock_session = MagicMock()
    mock_session.post.side_effect = real_requests.exceptions.Timeout("timeout")
    mock_requests.Session.return_value = mock_session

    api_config = {'base_url': 'https://api.example.com/v1', 'model': 'test-model'}
    with contextlib.redirect_stdout(io.StringIO()):
        result = _call_llm_api([{"role": "user", "content": "test"}], api_config, "key")
    assert result.success is False
    assert mock_session.post.call_count == 3
    assert all(
        call.kwargs["timeout"] == (10, 120)
        for call in mock_session.post.call_args_list
    )
    delays = [call.args[0] for call in mock_sleep.call_args_list]
    assert 3 <= delays[0] <= 4
    assert 8 <= delays[1] <= 9
    assert len(delays) == 2


def test_call_llm_api_missing_config():
    result = _call_llm_api([{"role": "user", "content": "test"}], {}, "key")
    assert result.success is False
    assert "incomplete" in result.reason.lower()


# === evaluate_batch (mocked LLM) ===

@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_batch_success_updates_candidate(mock_call, mock_sleep):
    mock_call.return_value = LLMEvalResult(
        success=True, adjustment=7, reason="高度匹配", model="test-model"
    )
    candidates = [
        {'name': '张三', 'match_score': 65, 'recommend_level': '推荐', 'summary': '5年Java'},
    ]
    result = quiet_evaluate_batch(candidates, "岗位要求", {'base_url': 'x', 'model': 'y'}, "key")
    c = result[0]
    assert c['llm_evaluated'] is True
    assert c['llm_adjustment'] == 7
    assert c['match_score'] == 72
    assert c['recommend_level'] == '推荐'
    assert c['rule_score'] == 65
    assert c['llm_model'] == 'test-model'


@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_batch_sends_compact_summary_to_llm(mock_call, mock_sleep):
    mock_call.return_value = LLMEvalResult(success=True, adjustment=0, reason="ok", model="m")
    candidates = [{
        'name': '张三',
        'match_score': 65,
        'recommend_level': '推荐',
        'summary': '工作职责：' + ('Python 项目经验。' * 500),
    }]

    quiet_evaluate_batch(candidates, "岗位要求", {'base_url': 'x', 'model': 'y'}, "key")

    messages = mock_call.call_args.args[0]
    prompt_text = messages[1]['content']
    assert "工作职责：Python 项目经验" in prompt_text
    assert len(prompt_text) < len(candidates[0]['summary'])


@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_batch_failure_preserves_score(mock_call, mock_sleep):
    mock_call.return_value = LLMEvalResult(success=False, reason="timeout")
    candidates = [
        {'name': '李四', 'match_score': 60, 'recommend_level': '待定', 'summary': '3年Python'},
    ]
    result = quiet_evaluate_batch(candidates, "岗位", {'base_url': 'x', 'model': 'y'}, "key")
    c = result[0]
    assert c['llm_evaluated'] is False
    assert c['llm_error'] == "timeout"
    assert c['match_score'] == 60
    assert c['recommend_level'] == '待定'


@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_batch_score_clamped_high(mock_call, mock_sleep):
    mock_call.return_value = LLMEvalResult(success=True, adjustment=10, reason="极好", model="m")
    candidates = [{'name': '王五', 'match_score': 95, 'recommend_level': '强烈推荐', 'summary': '10年'}]
    result = quiet_evaluate_batch(candidates, "岗位", {'base_url': 'x', 'model': 'y'}, "key")
    assert result[0]['match_score'] == 100


@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_batch_score_clamped_low(mock_call, mock_sleep):
    mock_call.return_value = LLMEvalResult(success=True, adjustment=-10, reason="不匹配", model="m")
    candidates = [{'name': '赵六', 'match_score': 55, 'recommend_level': '待定', 'summary': '1年'}]
    result = quiet_evaluate_batch(candidates, "岗位", {'base_url': 'x', 'model': 'y'}, "key")
    assert result[0]['match_score'] == 45
    assert result[0]['recommend_level'] == '已淘汰'


@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_batch_only_applies_verified_high_confidence_hard_failure(mock_call, mock_sleep):
    finding = {
        "condition": "工作经验不少于4年",
        "verdict": "fail",
        "evidence": "2026届应届生",
        "confidence": "high",
    }
    mock_call.return_value = LLMEvalResult(
        success=True,
        adjustment=-10,
        reason="经验不足",
        model="m",
        hard_condition_verdict="fail",
        hard_condition_findings=[finding],
    )
    candidates = [{
        'name': '应届生',
        'match_score': 65,
        'recommend_level': '推荐',
        'summary': '2026届应届生，Java 开发',
        'qualification_status': 'manual_review',
    }]
    result = quiet_evaluate_batch(
        candidates, "岗位", {'base_url': 'x', 'model': 'y'}, "key",
        hard_conditions="## 筛选硬条件\n- 经验：≥4年\n",
    )
    assert result[0]['qualification_status'] == 'rejected'
    assert result[0]['recommend_level'] == '已淘汰'


@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_batch_rejects_explicit_upgrade_bachelor_finding(mock_call, mock_sleep):
    finding = {
        "condition": "统招本科学历",
        "verdict": "fail",
        "evidence": "专升本",
        "confidence": "high",
    }
    mock_call.return_value = LLMEvalResult(
        success=True,
        adjustment=-6,
        reason="专升本不符合统招本科要求",
        model="m",
        hard_condition_verdict="fail",
        hard_condition_findings=[finding],
    )
    candidates = [{
        'name': '待确认',
        'match_score': 70,
        'recommend_level': '推荐',
        'summary': '专升本，Java 开发',
        'qualification_status': 'manual_review',
    }]
    result = quiet_evaluate_batch(
        candidates, "岗位", {'base_url': 'x', 'model': 'y'}, "key",
        hard_conditions="## 筛选硬条件\n- 必要条件：统招本科\n",
    )
    assert result[0]['qualification_status'] == 'rejected'
    assert result[0]['recommend_level'] == '已淘汰'


@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_batch_level_upgrade(mock_call, mock_sleep):
    mock_call.return_value = LLMEvalResult(success=True, adjustment=10, reason="极好", model="m")
    candidates = [{'name': 'A', 'match_score': 66, 'recommend_level': '推荐', 'summary': 'x'}]
    result = quiet_evaluate_batch(candidates, "岗位", {'base_url': 'x', 'model': 'y'}, "key")
    assert result[0]['match_score'] == 76
    assert result[0]['recommend_level'] == '强烈推荐'


@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_batch_max_candidates_limit(mock_call, mock_sleep):
    mock_call.return_value = LLMEvalResult(success=True, adjustment=0, reason="ok", model="m")
    candidates = [
        {'name': f'C{i}', 'match_score': 60, 'recommend_level': '待定', 'summary': 'x'}
        for i in range(10)
    ]
    quiet_evaluate_batch(candidates, "岗位", {'base_url': 'x', 'model': 'y'}, "key", max_candidates=3)
    assert mock_call.call_count == 3


@patch('llm_eval._call_llm_api')
def test_batch_stop_event_interrupts(mock_call):
    import contextlib, io
    mock_call.return_value = LLMEvalResult(success=True, adjustment=0, reason="ok", model="m")
    stop_event = threading.Event()
    stop_event.set()
    candidates = [
        {'name': f'C{i}', 'match_score': 60, 'recommend_level': '待定', 'summary': 'x'}
        for i in range(5)
    ]
    with contextlib.redirect_stdout(io.StringIO()):
        quiet_evaluate_batch(candidates, "岗位", {'base_url': 'x', 'model': 'y'}, "key",
                             stop_event=stop_event)
    assert mock_call.call_count == 0


def test_batch_empty_candidates():
    result = quiet_evaluate_batch([], "岗位", {}, "key")
    assert result == []


def test_eval_workers_use_five_for_official_api_and_three_for_relay():
    official_workers, official_relay = _resolve_eval_workers(
        {
            "api_provider": "qwen",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        },
        None,
    )
    relay_workers, relay_detected = _resolve_eval_workers(
        {
            "api_provider": "qwen",
            "base_url": "https://newapi.example.com/v1",
        },
        None,
    )

    assert official_workers == 5
    assert official_relay is False
    assert relay_workers == 3
    assert relay_detected is True


def test_request_timeout_uses_120_seconds_only_for_relay():
    official_timeout = _resolve_request_timeout({
        "api_provider": "qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    })
    relay_timeout = _resolve_request_timeout({
        "api_provider": "custom",
        "base_url": "https://newapi.example.com/v1",
    })

    assert official_timeout == (10, 60)
    assert relay_timeout == (10, 120)


@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_batch_progress_callback(mock_call, mock_sleep):
    mock_call.return_value = LLMEvalResult(success=True, adjustment=0, reason="ok", model="m")
    progress_calls = []
    def on_progress(pct, desc):
        progress_calls.append((pct, desc))
    candidates = [
        {'name': 'A', 'match_score': 60, 'recommend_level': '待定', 'summary': 'x'},
        {'name': 'B', 'match_score': 58, 'recommend_level': '待定', 'summary': 'y'},
    ]
    quiet_evaluate_batch(candidates, "岗位", {'base_url': 'x', 'model': 'y'}, "key",
                         progress_callback=on_progress)
    assert len(progress_calls) == 3
    assert "AI 评估中" in progress_calls[0][1]
    assert "0/2" in progress_calls[0][1]  # 初始进度
    assert "AI 评估中" in progress_calls[1][1]  # 第一个完成


# === P0: 扩展硬条件复核（年龄/薪资/求职状态/地点）===

@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_batch_rejects_age_exceeding_max(mock_call, mock_sleep):
    """AI 发现候选人年龄超限，经规则复核后淘汰"""
    finding = {
        "condition": "年龄超过岗位上限",
        "verdict": "fail",
        "evidence": "候选人35岁，岗位要求≤32岁",
        "confidence": "high",
    }
    mock_call.return_value = LLMEvalResult(
        success=True,
        adjustment=-5,
        reason="年龄超限",
        model="m",
        hard_condition_verdict="fail",
        hard_condition_findings=[finding],
    )
    candidates = [{
        'name': '大龄候选人',
        'match_score': 70,
        'recommend_level': '推荐',
        'summary': '候选人35岁，岗位要求≤32岁',
        'qualification_status': 'qualified',
    }]
    result = quiet_evaluate_batch(
        candidates, "岗位", {'base_url': 'x', 'model': 'y'}, "key",
        hard_conditions="## 筛选硬条件\n- 年龄：≤32岁\n",
        rule={'max_age': 32},
    )
    assert result[0]['qualification_status'] == 'rejected'
    assert result[0]['recommend_level'] == '已淘汰'


@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_batch_rejects_salary_exceeding_max(mock_call, mock_sleep):
    """AI 发现候选人期望薪资超限，经规则复核后淘汰"""
    finding = {
        "condition": "期望薪资超过岗位上限",
        "verdict": "fail",
        "evidence": "期望薪资25K，岗位最高20K",
        "confidence": "high",
    }
    mock_call.return_value = LLMEvalResult(
        success=True,
        adjustment=-3,
        reason="薪资不匹配",
        model="m",
        hard_condition_verdict="fail",
        hard_condition_findings=[finding],
    )
    candidates = [{
        'name': '高薪候选人',
        'match_score': 68,
        'recommend_level': '推荐',
        'summary': '期望薪资25K，岗位最高20K',
        'qualification_status': 'qualified',
    }]
    result = quiet_evaluate_batch(
        candidates, "岗位", {'base_url': 'x', 'model': 'y'}, "key",
        hard_conditions="## 筛选硬条件\n- 薪资上限：20K\n",
        rule={'salary_max': 20},
    )
    assert result[0]['qualification_status'] == 'rejected'
    assert result[0]['recommend_level'] == '已淘汰'


@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_batch_rejects_job_status_not_considering(mock_call, mock_sleep):
    """AI 发现候选人暂不考虑新机会，经规则复核后淘汰"""
    finding = {
        "condition": "求职状态暂不考虑",
        "verdict": "fail",
        "evidence": "暂不考虑新机会",
        "confidence": "high",
    }
    mock_call.return_value = LLMEvalResult(
        success=True,
        adjustment=-8,
        reason="暂不考虑",
        model="m",
        hard_condition_verdict="fail",
        hard_condition_findings=[finding],
    )
    candidates = [{
        'name': '暂不考虑候选人',
        'match_score': 72,
        'recommend_level': '推荐',
        'summary': '暂不考虑新机会',
        'qualification_status': 'qualified',
    }]
    result = quiet_evaluate_batch(
        candidates, "岗位", {'base_url': 'x', 'model': 'y'}, "key",
        hard_conditions="## 筛选硬条件\n",
        rule={},
    )
    assert result[0]['qualification_status'] == 'rejected'
    assert result[0]['recommend_level'] == '已淘汰'


@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_batch_rejects_location_mismatch(mock_call, mock_sleep):
    """AI 发现候选人期望城市不匹配，经规则复核后淘汰"""
    finding = {
        "condition": "期望城市与岗位不符",
        "verdict": "fail",
        "evidence": "候选人期望北京，岗位要求上海",
        "confidence": "high",
    }
    mock_call.return_value = LLMEvalResult(
        success=True,
        adjustment=-4,
        reason="地点不匹配",
        model="m",
        hard_condition_verdict="fail",
        hard_condition_findings=[finding],
    )
    candidates = [{
        'name': '异地候选人',
        'match_score': 66,
        'recommend_level': '推荐',
        'summary': '候选人期望北京，岗位要求上海',
        'qualification_status': 'qualified',
    }]
    result = quiet_evaluate_batch(
        candidates, "岗位", {'base_url': 'x', 'model': 'y'}, "key",
        hard_conditions="## 筛选硬条件\n- 地点：上海\n",
        rule={'work_location': '上海'},
    )
    assert result[0]['qualification_status'] == 'rejected'
    assert result[0]['recommend_level'] == '已淘汰'


@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_batch_keeps_age_within_limit(mock_call, mock_sleep):
    """AI 发现候选人年龄在限内，不淘汰"""
    finding = {
        "condition": "年龄在岗位上限内",
        "verdict": "pass",
        "evidence": "候选人28岁，岗位要求≤32岁",
        "confidence": "high",
    }
    mock_call.return_value = LLMEvalResult(
        success=True,
        adjustment=3,
        reason="匹配良好",
        model="m",
        hard_condition_verdict="pass",
        hard_condition_findings=[finding],
    )
    candidates = [{
        'name': '年轻候选人',
        'match_score': 70,
        'recommend_level': '推荐',
        'summary': '候选人28岁，岗位要求≤32岁',
        'qualification_status': 'qualified',
    }]
    result = quiet_evaluate_batch(
        candidates, "岗位", {'base_url': 'x', 'model': 'y'}, "key",
        hard_conditions="## 筛选硬条件\n- 年龄：≤32岁\n",
        rule={'max_age': 32},
    )
    assert result[0]['qualification_status'] == 'qualified'
    assert result[0]['match_score'] == 73


@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_batch_rejects_salary_borderline(mock_call, mock_sleep):
    """薪资恰好超 1K 淘汰（salary_max=20, 期望21K）"""
    finding = {
        "condition": "期望薪资超过岗位上限",
        "verdict": "fail",
        "evidence": "期望薪资21K，岗位最高20K",
        "confidence": "high",
    }
    mock_call.return_value = LLMEvalResult(
        success=True,
        adjustment=-2,
        reason="薪资略高",
        model="m",
        hard_condition_verdict="fail",
        hard_condition_findings=[finding],
    )
    candidates = [{
        'name': '薪资边界候选人',
        'match_score': 65,
        'recommend_level': '推荐',
        'summary': '期望薪资21K，岗位最高20K',
        'qualification_status': 'qualified',
    }]
    result = quiet_evaluate_batch(
        candidates, "岗位", {'base_url': 'x', 'model': 'y'}, "key",
        hard_conditions="## 筛选硬条件\n- 薪资上限：20K\n",
        rule={'salary_max': 20},
    )
    assert result[0]['qualification_status'] == 'rejected'


# === 维度评分解析 ===

def test_parse_response_with_dimension_scores():
    text = json.dumps({
        "adjustment": 5,
        "reason": "匹配良好",
        "hard_condition_verdict": "pass",
        "hard_condition_findings": [],
        "dimension_scores": {
            "skill_depth": 8,
            "experience_quality": 7,
            "industry_fit": 6,
            "growth_potential": 9,
        },
    }, ensure_ascii=False)
    result = _parse_response(text)
    assert result['dimension_scores']['skill_depth'] == 8
    assert result['dimension_scores']['experience_quality'] == 7
    assert result['dimension_scores']['industry_fit'] == 6
    assert result['dimension_scores']['growth_potential'] == 9


def test_parse_response_dimension_scores_clamped():
    text = json.dumps({
        "adjustment": 0,
        "reason": "ok",
        "hard_condition_verdict": "unknown",
        "hard_condition_findings": [],
        "dimension_scores": {
            "skill_depth": 15,
            "experience_quality": 0,
            "industry_fit": -5,
            "growth_potential": 10,
        },
    }, ensure_ascii=False)
    result = _parse_response(text)
    assert result['dimension_scores']['skill_depth'] == 10
    assert result['dimension_scores']['experience_quality'] == 1
    assert result['dimension_scores']['industry_fit'] == 1
    assert result['dimension_scores']['growth_potential'] == 10


def test_parse_response_dimension_scores_missing():
    text = json.dumps({
        "adjustment": 0,
        "reason": "ok",
        "hard_condition_verdict": "unknown",
        "hard_condition_findings": [],
    }, ensure_ascii=False)
    result = _parse_response(text)
    assert result['dimension_scores'] == {}


@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_batch_stores_dimension_scores(mock_call, mock_sleep):
    mock_call.return_value = LLMEvalResult(
        success=True, adjustment=5, reason="匹配", model="m",
        dimension_scores={"skill_depth": 8, "experience_quality": 7, "industry_fit": 6, "growth_potential": 9},
    )
    candidates = [{'name': '张三', 'match_score': 65, 'recommend_level': '推荐', 'summary': '5年Java'}]
    result = quiet_evaluate_batch(candidates, "岗位", {'base_url': 'x', 'model': 'y'}, "key")
    dims = result[0].get('llm_dimension_scores')
    assert dims is not None
    assert dims['skill_depth'] == 8
    assert dims['experience_quality'] == 7
    assert dims['industry_fit'] == 6
    assert dims['growth_potential'] == 9


# === 简历二次评估固化 rule_score（regression: 未跑一次评估却导入简历时 rule_score 缺失，撤回时无法还原）===

@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_evaluate_with_resume_captures_rule_score_when_missing(mock_call, mock_sleep):
    """候选人未跑过一次 AI 评估（rule_score 缺失）时，简历评估须固化 rule_score 供撤回还原。"""
    mock_call.return_value = LLMEvalResult(
        success=True, adjustment=5, reason="匹配", model="m",
    )
    candidate = {
        'name': '张三', 'match_score': 65, 'recommend_level': '推荐',
        'summary': '5年Java',
        'score_breakdown': {'base': 25, 'skill': 30, 'experience': 5, 'education': 5, 'preferred': 0, 'total': 65},
    }
    evaluate_with_resume(candidate, "简历文本", "岗位", {'base_url': 'x', 'model': 'y'}, "key")
    assert candidate['rule_score'] == 65, "简历评估应固化 rule_score=65（评估前的真实规则分）"
    assert candidate['match_score'] == 70  # 65 + 5（替代，不叠加）
    assert candidate['resume_eval_adjustment'] == 5


@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_evaluate_with_resume_preserves_existing_rule_score(mock_call, mock_sleep):
    """候选人已跑过一次评估（rule_score 已存在）时，简历评估不得覆盖 rule_score。"""
    mock_call.return_value = LLMEvalResult(
        success=True, adjustment=5, reason="匹配", model="m",
    )
    candidate = {
        'name': '李四', 'match_score': 73, 'recommend_level': '推荐',  # 65(rule) + 8(一次评估)
        'rule_score': 65, 'llm_adjustment': 8, 'llm_evaluated': True,
        'summary': '5年Java',
        'score_breakdown': {'base': 25, 'skill': 30, 'experience': 5, 'education': 5, 'preferred': 0,
                            'ai_adjustment': 8, 'total': 73},
    }
    evaluate_with_resume(candidate, "简历文本", "岗位", {'base_url': 'x', 'model': 'y'}, "key")
    assert candidate['rule_score'] == 65, "已固化的 rule_score 不应被覆盖"
    assert candidate['match_score'] == 70  # 替代：65 + 5，而非 65+8+5
    assert candidate['resume_eval_adjustment'] == 5


# === 简历二次评估的维度评分（独立字段，不覆盖一次评估的）===

@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_evaluate_with_resume_stores_resume_dimension_scores(mock_call, mock_sleep):
    """简历评估的维度评分存到独立字段 resume_eval_dimension_scores，不覆盖一次评估的 llm_dimension_scores。"""
    mock_call.return_value = LLMEvalResult(
        success=True, adjustment=5, reason="匹配", model="m",
        dimension_scores={"skill_depth": 9, "experience_quality": 8, "industry_fit": 7, "growth_potential": 9},
    )
    round1_dims = {"skill_depth": 5, "experience_quality": 5, "industry_fit": 5, "growth_potential": 5}
    candidate = {
        'name': '张三', 'match_score': 73, 'recommend_level': '推荐',
        'rule_score': 65, 'llm_adjustment': 8, 'llm_evaluated': True,
        'llm_dimension_scores': dict(round1_dims),
        'summary': '5年Java',
        'score_breakdown': {'base': 25, 'skill': 30, 'experience': 5, 'education': 5, 'preferred': 0,
                            'ai_adjustment': 8, 'total': 73},
    }
    evaluate_with_resume(candidate, "简历文本", "岗位", {'base_url': 'x', 'model': 'y'}, "key")
    assert candidate['resume_eval_dimension_scores'] == {
        "skill_depth": 9, "experience_quality": 8, "industry_fit": 7, "growth_potential": 9,
    }
    # 一次评估的维度评分必须保留，供撤回简历评估时还原
    assert candidate['llm_dimension_scores'] == round1_dims


@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_evaluate_with_resume_no_dimension_scores_keeps_round1(mock_call, mock_sleep):
    """简历评估未返回维度评分时不设 resume_eval_dimension_scores，显示回退到一次评估的。"""
    mock_call.return_value = LLMEvalResult(
        success=True, adjustment=5, reason="匹配", model="m",
        dimension_scores={},
    )
    round1_dims = {"skill_depth": 5, "experience_quality": 5, "industry_fit": 5, "growth_potential": 5}
    candidate = {
        'name': '张三', 'match_score': 73, 'rule_score': 65, 'llm_adjustment': 8,
        'llm_dimension_scores': dict(round1_dims),
        'summary': '5年Java',
        'score_breakdown': {'base': 25, 'skill': 30, 'experience': 5, 'education': 5, 'preferred': 0,
                            'ai_adjustment': 8, 'total': 73},
    }
    evaluate_with_resume(candidate, "简历文本", "岗位", {'base_url': 'x', 'model': 'y'}, "key")
    assert 'resume_eval_dimension_scores' not in candidate
    assert candidate['llm_dimension_scores'] == round1_dims


# === evaluate_batch 基线读取（regression: 张力1——用真实 rule_score，不读 match_score；有简历评估不改写最终分）===

@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_batch_does_not_overwrite_resume_score(mock_call, mock_sleep):
    """已有简历二次评估的候选人跑一次评估时，不得改写最终分（resume 替代语义）；
    一次评估仅记录 llm_adjustment/维度评分/硬条件复核，match_score 保持 rule+resume_adj。"""
    mock_call.return_value = LLMEvalResult(
        success=True, adjustment=8, reason="匹配", model="m",
        dimension_scores={"skill_depth": 7, "experience_quality": 6, "industry_fit": 5, "growth_potential": 7},
    )
    # rule=65，简历评估 +5 → match_score=70；现在再跑一次评估 +8
    candidates = [{
        'name': '张三', 'match_score': 70, 'recommend_level': '推荐',
        'rule_score': 65,
        'resume_eval_adjustment': 5,
        'summary': '5年Java',
        'score_breakdown': {'base': 25, 'skill': 30, 'experience': 5, 'education': 5, 'preferred': 0,
                            'resume_adjustment': 5, 'total': 70},
    }]
    result = quiet_evaluate_batch(
        candidates, "岗位", {'base_url': 'x', 'model': 'y'}, "key",
    )
    c = result[0]
    # 最终分不变（resume 替代，不叠一次评估的 +8）
    assert c['match_score'] == 70, f"一次评估不得改写简历评估的最终分：{c['match_score']}"
    # 一次评估元数据已记录
    assert c['llm_evaluated'] is True
    assert c['llm_adjustment'] == 8
    assert c['llm_dimension_scores'] == {"skill_depth": 7, "experience_quality": 6, "industry_fit": 5, "growth_potential": 7}
    # rule_score 保持原值（不被污染为 match_score）
    assert c['rule_score'] == 65
    # breakdown: ai_adjustment 记一次评估值，total 保持 70（= rule+resume_adj，拆解合计=总分）
    assert c['score_breakdown']['ai_adjustment'] == 8
    assert c['score_breakdown']['total'] == 70
    assert c['score_breakdown']['resume_adjustment'] == 5


@patch('llm_eval.time.sleep')
@patch('llm_eval._call_llm_api')
def test_batch_uses_pristine_rule_score_no_stacking(mock_call, mock_sleep):
    """对已有一次评估的候选人重跑一次评估：用已固化的 rule_score 作基线，不叠加旧调整值。"""
    mock_call.return_value = LLMEvalResult(
        success=True, adjustment=-3, reason="略差", model="m",
    )
    # rule=65，旧一次评估 +8 → match_score=73；现在重跑一次评估 -3
    candidates = [{
        'name': '李四', 'match_score': 73, 'recommend_level': '推荐',
        'rule_score': 65, 'llm_evaluated': True, 'llm_adjustment': 8,
        'summary': '5年Java',
        'score_breakdown': {'base': 25, 'skill': 30, 'experience': 5, 'education': 5, 'preferred': 0,
                            'ai_adjustment': 8, 'total': 73},
    }]
    result = quiet_evaluate_batch(
        candidates, "岗位", {'base_url': 'x', 'model': 'y'}, "key",
    )
    c = result[0]
    # 新分 = rule(65) + (-3) = 62，不是 73 + (-3) = 70（不叠加旧 +8）
    assert c['match_score'] == 62, f"应用 rule_score 作基线，不应叠加旧调整：{c['match_score']}"
    assert c['llm_adjustment'] == -3
    assert c['rule_score'] == 65  # 不被覆写为 73
    assert c['score_breakdown']['ai_adjustment'] == -3
    assert c['score_breakdown']['total'] == 62
