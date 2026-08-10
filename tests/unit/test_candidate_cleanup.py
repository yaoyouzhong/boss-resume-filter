from candidate_cleanup import clear_candidates_in_place


def test_current_job_cleanup_keeps_greeted_blacklisted_and_other_jobs():
    candidates = [
        {
            "geek_id": "greeted",
            "job_name": "Java 工程师",
            "greet_sent": True,
        },
        {
            "geek_id": "blacklisted",
            "job_name": "Java 工程师",
            "blacklisted": True,
        },
        {"geek_id": "remove", "job_name": "Java 工程师"},
        {"geek_id": "other", "job_name": "Python 工程师"},
    ]

    outcome = clear_candidates_in_place(
        candidates,
        scope="current",
        selected_job=" Java   工程师 ",
        keep_greeted=True,
    )

    assert [candidate["geek_id"] for candidate in candidates] == [
        "other",
        "greeted",
        "blacklisted",
    ]
    assert outcome.removed_count == 1
    assert outcome.greeted_kept_count == 1
    assert outcome.blacklist_kept_count == 1


def test_all_job_cleanup_without_greeted_retention_keeps_only_blacklist():
    candidates = [
        {"geek_id": "plain", "job_name": "Java 工程师"},
        {
            "geek_id": "greeted",
            "job_name": "Python 工程师",
            "greet_sent": True,
        },
        {
            "geek_id": "blacklisted",
            "job_name": "Java 工程师",
            "greet_sent": True,
            "blacklisted": True,
        },
    ]

    outcome = clear_candidates_in_place(
        candidates,
        scope="all",
        selected_job="全部岗位",
        keep_greeted=False,
    )

    assert [candidate["geek_id"] for candidate in candidates] == [
        "blacklisted"
    ]
    assert outcome.removed_count == 2
    assert outcome.greeted_kept_count == 0
    assert outcome.blacklist_kept_count == 1


def test_candidate_cleanup_rejects_unknown_scope_without_mutation():
    candidates = [{"geek_id": "keep", "job_name": "Java 工程师"}]

    try:
        clear_candidates_in_place(
            candidates,
            scope="unknown",
            selected_job="Java 工程师",
            keep_greeted=True,
        )
    except ValueError as exc:
        assert "Unsupported candidate cleanup scope" in str(exc)
    else:
        raise AssertionError("unknown cleanup scope should fail")

    assert candidates == [{"geek_id": "keep", "job_name": "Java 工程师"}]
