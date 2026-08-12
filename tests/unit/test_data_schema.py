"""Pure migration tests for persisted job and candidate identities."""
from data_schema import (
    CANDIDATE_SCHEMA_VERSION,
    JOB_CONFIG_SCHEMA_VERSION,
    canonical_candidate_identity,
    job_uuid_by_normalized_name,
    migrate_candidate_records,
    upgrade_job_config,
)


JOB_UUID_A = "11111111-1111-4111-8111-111111111111"
JOB_UUID_B = "22222222-2222-4222-8222-222222222222"


def test_candidate_identity_prefers_job_uuid_and_falls_back_to_legacy_name():
    stable = canonical_candidate_identity({
        "geek_id": " g1 ",
        "job_uuid": JOB_UUID_A,
        "job_name": "Java 工程师",
    })
    renamed = canonical_candidate_identity({
        "geek_id": "g1",
        "job_uuid": JOB_UUID_A,
        "job_name": "高级 Java 工程师",
    })
    legacy = canonical_candidate_identity({
        "geek_id": "g1",
        "job_name": " Java 工程师 ",
    })

    assert stable == renamed == ("g1", f"uuid:{JOB_UUID_A}")
    assert legacy == ("g1", "Java工程师")
    assert canonical_candidate_identity({
        "geek_id": "g1",
        "job_uuid": JOB_UUID_B,
        "job_name": "Java 工程师",
    }) != stable


def _legacy_config() -> dict:
    return {
        "job_requirements": {
            "Java 工程师": {"min_exp": 3},
            "Python工程师": {"min_exp": 2},
        }
    }


def test_job_config_upgrade_is_deterministic_and_idempotent():
    first, changed = upgrade_job_config(_legacy_config())
    second, changed_again = upgrade_job_config(first)

    assert changed is True
    assert changed_again is False
    assert first == second
    assert first["schema_version"] == JOB_CONFIG_SCHEMA_VERSION
    assert first["job_requirements"]["Java 工程师"]["job_uuid"]
    assert (
        first["job_requirements"]["Java 工程师"]["job_uuid"]
        != first["job_requirements"]["Python工程师"]["job_uuid"]
    )


def test_job_config_rename_preserves_existing_stable_id():
    upgraded, _ = upgrade_job_config(_legacy_config())
    rule = upgraded["job_requirements"].pop("Java 工程师")
    upgraded["job_requirements"]["高级 Java 工程师"] = rule

    renamed, _ = upgrade_job_config(upgraded)

    assert (
        renamed["job_requirements"]["高级 Java 工程师"]["job_uuid"]
        == rule["job_uuid"]
    )


def test_job_config_rejects_ambiguous_normalized_names():
    config = {
        "job_requirements": {
            "Java 工程师": {},
            "Java工程师": {},
        }
    }
    try:
        upgrade_job_config(config)
    except ValueError as exc:
        assert "归一化后重复" in str(exc)
    else:
        raise AssertionError("ambiguous normalized names must be rejected")


def test_candidate_migration_maps_exact_job_names_and_reports_unresolved():
    config = _legacy_config()
    candidates = [
        {"geek_id": "g1", "job_name": "Java工程师"},
        {"geek_id": "g2", "job_name": "未知岗位"},
    ]

    migrated, unresolved = migrate_candidate_records(candidates, config)
    name_map = job_uuid_by_normalized_name(config)

    assert migrated[0]["job_uuid"] == name_map["java工程师"]
    assert migrated[0]["schema_version"] == CANDIDATE_SCHEMA_VERSION
    assert "job_uuid" not in migrated[1]
    assert unresolved == [{"geek_id": "g2", "job_name": "未知岗位"}]
    assert "job_uuid" not in candidates[0]


def test_candidate_migration_rejects_future_schema():
    try:
        migrate_candidate_records(
            [{
                "geek_id": "g1",
                "job_name": "Java 工程师",
                "schema_version": CANDIDATE_SCHEMA_VERSION + 1,
            }],
            _legacy_config(),
        )
    except ValueError as exc:
        assert "更高版本" in str(exc)
    else:
        raise AssertionError("future candidate schemas must be rejected")


def test_candidate_migration_updates_display_name_after_job_rename():
    upgraded, _ = upgrade_job_config(_legacy_config())
    rule = upgraded["job_requirements"].pop("Java 工程师")
    upgraded["job_requirements"]["高级 Java 工程师"] = rule
    candidate = {
        "geek_id": "g1",
        "job_name": "Java 工程师",
        "job_uuid": rule["job_uuid"],
    }

    migrated, unresolved = migrate_candidate_records(
        [candidate],
        upgraded,
    )

    assert unresolved == []
    assert migrated[0]["job_name"] == "高级 Java 工程师"
