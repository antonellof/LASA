"""Prevent legacy extraction artifacts and audit rates becoming validated claims."""

from types import SimpleNamespace

import pytest

from lasa.authorization import AuthorizationRecord
from lasa.pipeline import verify


def test_verify_rejects_legacy_direction_before_loading_model(monkeypatch):
    record = AuthorizationRecord(
        id="test", operator="tester", scope=("owned/a",),
        basis="owned", authorized_on="2026-11-08",
    )
    artifacts = {
        "load": {"models": []},
        "attack_base": {"attacks": [], "refusal_direction_meta": {"direction": [1.0]}},
        "predict": {"variants": []},
    }
    ctx = SimpleNamespace(
        target_model_ids=["owned/a"], authorization=record,
        read_artifact=lambda stage: artifacts[stage],
    )
    monkeypatch.setattr(verify, "_load_variant_model", lambda *_: pytest.fail("loaded legacy model"))
    with pytest.raises(ValueError, match="superseded activation extraction"):
        verify.run(ctx)


def test_unauthorized_verify_still_checks_gate_before_artifacts():
    ctx = SimpleNamespace(target_model_ids=["owned/a"], authorization=None)
    with pytest.raises(PermissionError):
        verify.run(ctx)
