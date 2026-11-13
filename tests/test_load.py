"""Checkpoint hashing and local path resolution on a fake checkpoint directory."""

import pytest

from lasa.pipeline.load import hash_checkpoint, resolve_local_path


def _make_ckpt(path, weights=b"w" * 100):
    path.mkdir(parents=True)
    (path / "config.json").write_text('{"hidden_size": 8}')
    (path / "model.safetensors").write_bytes(weights)


def test_resolve_local_path_default(tmp_path):
    p = resolve_local_path({"model_id": "org/name"}, tmp_path)
    assert p == tmp_path / "org__name"


def test_resolve_local_path_explicit(tmp_path):
    p = resolve_local_path({"model_id": "org/name", "path": "/x/y"}, tmp_path)
    assert str(p) == "/x/y"


def test_hash_is_stable(tmp_path):
    _make_ckpt(tmp_path / "a")
    assert hash_checkpoint(tmp_path / "a")["digest"] == hash_checkpoint(tmp_path / "a")["digest"]


def test_hash_changes_with_weights(tmp_path):
    _make_ckpt(tmp_path / "a", b"w" * 100)
    _make_ckpt(tmp_path / "b", b"x" * 100)
    assert hash_checkpoint(tmp_path / "a")["digest"] != hash_checkpoint(tmp_path / "b")["digest"]


def test_hash_rejects_missing_weights(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    (d / "config.json").write_text("{}")
    with pytest.raises(FileNotFoundError):
        hash_checkpoint(d)


def test_hash_rejects_missing_dir(tmp_path):
    with pytest.raises(FileNotFoundError):
        hash_checkpoint(tmp_path / "nope")
