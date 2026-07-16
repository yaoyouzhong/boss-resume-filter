"""Intent-level tests for persistent candidate contact queue state."""
import json
import tempfile
from pathlib import Path

from contact_queue import (
    build_contact_queue_item,
    candidate_identity,
    load_contact_queue,
    save_contact_queue,
)


def _candidate(**overrides):
    candidate = {
        "geek_id": "g1",
        "job_name": "Java Engineer",
        "name": "Candidate A",
        "match_score": 80,
    }
    candidate.update(overrides)
    return candidate


def test_candidate_identity_normalizes_job_spaces():
    assert candidate_identity(_candidate(job_name="Java Engineer")) == (
        "g1",
        "JavaEngineer",
    )


def test_queue_persists_intent_without_candidate_profile():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "contact_queue.json"
        item = build_contact_queue_item(_candidate(summary="private resume text"), now="20260716_090000")

        save_contact_queue([item], path)

        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["version"] == 1
        assert payload["items"][0]["geek_id"] == "g1"
        assert "candidate" not in payload["items"][0]
        assert "private resume text" not in path.read_text(encoding="utf-8")


def test_queue_restore_binds_latest_candidate_record():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "contact_queue.json"
        original = _candidate(name="Old Name")
        save_contact_queue([build_contact_queue_item(original)], path)
        latest = _candidate(name="Current Name", match_score=76)

        restored = load_contact_queue([latest], path)

        assert len(restored) == 1
        assert restored[0]["candidate"] is latest
        assert restored[0]["candidate"]["name"] == "Current Name"


def test_interrupted_sending_restores_as_manual_verification_not_retry():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "contact_queue.json"
        item = build_contact_queue_item(_candidate())
        item["status"] = "发送中"
        save_contact_queue([item], path)

        restored = load_contact_queue([_candidate()], path)

        assert restored[0]["status"] == "待核实"
        assert "核实" in restored[0]["message"]


def test_candidate_pending_state_overrides_saved_retryable_state():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "contact_queue.json"
        item = build_contact_queue_item(_candidate())
        item["status"] = "发送失败"
        save_contact_queue([item], path)
        pending = _candidate(
            greet_confirmation_pending=True,
            greet_confirmation_reason="button did not change",
        )

        restored = load_contact_queue([pending], path)

        assert restored[0]["status"] == "待核实"
        assert restored[0]["message"] == "button did not change"


def test_pending_candidate_restores_without_existing_queue_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "contact_queue.json"
        pending = _candidate(
            greet_confirmation_pending=True,
            greet_confirmation_reason="verify in BOSS chat list",
        )

        restored = load_contact_queue([pending], path)

        assert len(restored) == 1
        assert restored[0]["candidate"] is pending
        assert restored[0]["status"] == "待核实"
        assert restored[0]["source"] == "candidate_state"
        assert restored[0]["message"] == "verify in BOSS chat list"


def test_terminal_items_do_not_reappear_after_restart():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "contact_queue.json"
        sent = build_contact_queue_item(_candidate())
        sent["status"] = "已发送"

        save_contact_queue([sent], path)

        assert load_contact_queue([_candidate()], path) == []


def test_queue_load_recovers_valid_backup_after_corruption():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "contact_queue.json"
        item = build_contact_queue_item(_candidate())
        save_contact_queue([item], path)
        save_contact_queue([item], path)
        path.write_text("{broken", encoding="utf-8")

        restored = load_contact_queue([_candidate()], path)

        assert len(restored) == 1
        assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1
