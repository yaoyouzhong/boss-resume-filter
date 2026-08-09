from dataclasses import dataclass

import candidate_diagnostics_presenter


@dataclass
class Issue:
    title: str
    detail: str = ""


def test_diagnostic_key_info_shows_missing_greeting_facts():
    text = candidate_diagnostics_presenter.format_state_issue_key_info(
        Issue("打招呼记录不完整"),
        {"greet_sent": True},
    )
    assert text == "缺少：发送时间、发送方式"


def test_diagnostic_key_info_prefers_candidate_specific_review_reason():
    text = candidate_diagnostics_presenter.format_state_issue_key_info(
        Issue("需要人工确认"),
        {"qualification_reasons": ["学历信息需要人工核实"]},
    )
    assert text == "待确认：学历信息需要人工核实"


def test_clip_table_text_normalizes_whitespace_and_bounds_length():
    assert candidate_diagnostics_presenter.clip_table_text("a  b\n c", 20) == "a b c"
    assert candidate_diagnostics_presenter.clip_table_text("123456", 5) == "1234…"
