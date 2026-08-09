import contact_presenter


def test_contact_readiness_distinguishes_direct_and_page_send():
    direct = {"greet_context": {"chat_start": {"jid": "j1"}}}
    page = {"job_name": "Java 工程师"}
    assert contact_presenter.greet_queue_readiness_label(direct) == "已就绪"
    assert contact_presenter.greet_queue_readiness_label(page) == "发送时检查"
    assert "Java 工程师" in contact_presenter.greet_queue_readiness_tooltip(page)


def test_contact_selection_and_confirmation_content_keep_scope_visible():
    selected = [
        {"status": "待发送", "candidate": {"name": "张三"}},
        {"status": "待发送", "candidate": {"name": "李四"}},
    ]
    assert contact_presenter.greet_queue_selection_text(selected) == (
        "已选 2 人：张三、李四 · 待发送"
    )

    headline, message = contact_presenter.build_greet_queue_confirmation_content(
        [
            {
                "candidate": {
                    "name": "张三",
                    "greet_context": {"chat_start": {"jid": "j1"}},
                }
            },
            {"candidate": {"name": "李四", "job_name": "Java 工程师"}},
        ]
    )
    assert headline == "联系 2 名候选人？"
    assert "1 人无需切换" in message
    assert "Java 工程师（1 人）" in message


def test_boss_page_classification_is_host_scoped():
    assert contact_presenter.is_boss_recommend_url(
        "https://www.zhipin.com/web/chat/recommend"
    )
    assert not contact_presenter.is_boss_recommend_url(
        "https://example.com/web/chat/recommend"
    )
    assert contact_presenter.is_boss_login_page(
        "https://www.zhipin.com/",
        "微信扫码登录",
    )


def test_revalidation_and_run_feedback_preserve_safe_queue_states():
    assert contact_presenter.revalidate_greet_queue_candidate(
        {"geek_id": "g1", "greet_sent": True}
    ) == ("已发送", "本地已标记为已沟通")

    title, headline, message, level = contact_presenter.build_greet_queue_run_feedback(
        {"success": 1, "failed": 1}
    )
    assert title == "发送结果"
    assert headline == "发送部分完成"
    assert "失败：1 人" in message
    assert level == "warning"
