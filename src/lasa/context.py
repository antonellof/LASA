"""Run context shared by pipeline stages.

A stage reads its inputs from `out_dir` and writes its artifact there. The
context holds only what every stage needs: the parsed config, the run id, the
artifact directory, and the authorization record.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from lasa.authorization import AuthorizationRecord, load_record
from lasa.schemas import validate_artifact


@dataclass
class Context:
    config: dict[str, Any]
    out_dir: Path
    authorization: AuthorizationRecord | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def run_id(self) -> str:
        return self.config["run_id"]

    @property
    def seed(self) -> int:
        return int(self.config.get("seed", 0))

    @property
    def base_model_id(self) -> str:
        return self.config["base"]["model_id"]

    @property
    def variant_model_ids(self) -> list[str]:
        return [v["model_id"] for v in self.config.get("variants", [])]

    @property
    def target_model_ids(self) -> list[str]:
        """Models that verify would send attacks to. Base plus variants."""
        return [self.base_model_id, *self.variant_model_ids]

    def artifact(self, stage: str) -> Path:
        return self.out_dir / f"{stage}.json"

    def write_artifact(self, stage: str, payload: dict[str, Any]) -> Path:
        validate_artifact(stage, payload)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.artifact(stage)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return path

    def read_artifact(self, stage: str) -> dict[str, Any]:
        path = self.artifact(stage)
        if not path.exists():
            raise FileNotFoundError(f"missing artifact for stage '{stage}': {path}")
        return json.loads(path.read_text())


def load_context(config_path: str | Path, out_root: str | Path = "reports") -> Context:
    config_path = Path(config_path)
    config = yaml.safe_load(config_path.read_text())
    report_cfg = config.get("report", {})
    if "out_dir" in report_cfg:
        out_dir = Path(report_cfg["out_dir"])
    else:
        out_dir = Path(out_root) / config["run_id"]
    auth_path = config.get("authorization")
    authorization = load_record(auth_path) if auth_path else None
    return Context(config=config, out_dir=out_dir, authorization=authorization)
