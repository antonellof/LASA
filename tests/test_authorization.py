"""The gate is the one thing that must never regress. Test it first."""

import pytest

from lasa.authorization import AuthorizationRecord, is_authorized, require_authorized

REC = AuthorizationRecord(
    id="t",
    operator="tester",
    scope=("owned/model-a",),
    basis="owned",
    authorized_on="2026-11-07",
)


def test_authorized_model_passes():
    assert is_authorized("owned/model-a", REC)


def test_unauthorized_model_fails():
    assert not is_authorized("someone-else/model-b", REC)


def test_require_raises_on_unauthorized():
    with pytest.raises(PermissionError):
        require_authorized(["someone-else/model-b"], REC)


def test_require_raises_when_record_is_none():
    with pytest.raises(PermissionError):
        require_authorized(["owned/model-a"], None)


def test_load_record_validates_schema(tmp_path):
    import jsonschema
    from lasa.authorization import load_record

    bad = tmp_path / "bad.json"
    bad.write_text('{"id": "x", "operator": "y", "scope": [], "basis": "owned", "authorized_on": "2026-11-07"}')
    with pytest.raises(jsonschema.ValidationError):
        load_record(bad)
