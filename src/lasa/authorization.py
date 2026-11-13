"""Authorization gate.

The verify stage must not run against any model that is not covered by a
valid authorization record. This module loads records and answers whether a
given model id is authorized. Keep this strict. It is what makes the tool
legitimate, not a convenience check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from importlib.resources import files

import jsonschema

ALLOWED_BASES = {"owned", "public-research", "written-authorization"}

def authorization_schema() -> dict:
    # Package data is present in wheels as well as editable checkouts. Never skip validation.
    return json.loads(files("lasa.schemas").joinpath("authorization.schema.json").read_text())


@dataclass(frozen=True)
class AuthorizationRecord:
    id: str
    operator: str
    scope: tuple[str, ...]
    basis: str
    authorized_on: str
    notes: str = ""


def load_record(path: str | Path) -> AuthorizationRecord:
    data = json.loads(Path(path).read_text())
    jsonschema.validate(instance=data, schema=authorization_schema(), format_checker=jsonschema.FormatChecker())
    basis = data.get("basis")
    if basis not in ALLOWED_BASES:
        raise ValueError(f"authorization basis not allowed: {basis!r}")
    return AuthorizationRecord(
        id=data["id"],
        operator=data["operator"],
        scope=tuple(data["scope"]),
        basis=basis,
        authorized_on=data["authorized_on"],
        notes=data.get("notes", ""),
    )


def is_authorized(model_id: str, record: AuthorizationRecord | None) -> bool:
    if not isinstance(record, AuthorizationRecord) or not isinstance(record.scope, tuple):
        return False
    # A directly constructed record must not turn scope membership into substring matching.
    if not all(isinstance(mid, str) and mid for mid in record.scope):
        return False
    return record.basis in ALLOWED_BASES and model_id in record.scope


def require_authorized(model_ids: list[str], record: AuthorizationRecord | None) -> None:
    """Raise if any target is not authorized. Called at the start of verify."""
    if record is None:
        raise PermissionError("verify refused. no authorization record provided.")
    missing = [m for m in model_ids if not is_authorized(m, record)]
    if missing:
        raise PermissionError(
            "verify refused. these targets are not in an authorized scope: "
            + ", ".join(missing)
        )
