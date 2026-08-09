import run_presenter


def test_terminal_run_text_keeps_progress_short_and_log_single_line():
    desc = "[扫描中断] 本轮共筛选 20 人\n最终保留：8 人"
    assert run_presenter.format_terminal_progress_text(desc) == (
        "扫描中断，已保存当前结果；详见下方摘要"
    )
    assert run_presenter.format_terminal_log_text(desc) == (
        "扫描中断，已保存当前结果"
    )


def test_run_summary_rows_and_contact_count_replacement():
    assert run_presenter.estimate_run_summary_rows("a" * 61, 60) == 2
    assert run_presenter.replace_run_summary_contact_queue_count(
        "最终保留：8 人\n本轮打招呼：3 人",
        5,
    ) == "最终保留：8 人\n本轮已加联系清单：5 人"
