from unittest.mock import patch

from education_tool_security import probe_education_credential_backend


def test_credential_backend_probe_is_read_only():
    with patch(
        "education_tool_security._credential_get_password",
        return_value=None,
    ) as getter:
        probe_education_credential_backend()

    getter.assert_called_once()
    service, username = getter.call_args.args
    assert service.endswith(".PackagingSmokeTest")
    assert username == "missing-credential"
