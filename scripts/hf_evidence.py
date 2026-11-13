"""Operator-side handling of the private evidence dataset (ADR-0022).

  python scripts/hf_evidence.py create <repo>           create it, private
  python scripts/hf_evidence.py pull <repo> <config>    copy a study's files into reports/
  python scripts/hf_evidence.py delete <repo>           remove it after the pull
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import yaml
from huggingface_hub import HfApi, snapshot_download


def main(argv) -> int:
    action, repo = argv[0], argv[1]
    api = HfApi()
    if action == "create":
        api.create_repo(repo, repo_type="dataset", private=True, exist_ok=True)
        if api.repo_info(repo, repo_type="dataset").private is not True:
            raise SystemExit("stop: dataset exists and is not private")
    elif action == "pull":
        cfg = yaml.safe_load(Path(argv[2]).read_text())
        out, study_id = Path(cfg["output_dir"]), cfg["study_id"]
        src = Path(snapshot_download(repo_id=repo, repo_type="dataset", allow_patterns=[f"{study_id}/*"])) / study_id
        out.mkdir(parents=True, exist_ok=True, mode=0o700)
        out.chmod(0o700)
        for f in sorted(src.iterdir()):
            if f.name.startswith("human_labels") and (out / f.name).exists():
                continue  # never replace an annotator's sheet
            shutil.copyfile(f, out / f.name)
            (out / f.name).chmod(0o600)
        commitment = Path(cfg.get("commitment_dir", f"experiments/results/{study_id}")) / "plan_commitment.json"
        if (out / "plan_commitment.json").exists() and not commitment.exists():
            commitment.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out / "plan_commitment.json", commitment)
            print(f"wrote {commitment}: commit and push it before the run stage")
        print(f"pulled {study_id} into {out}")
    elif action == "delete":
        api.delete_repo(repo, repo_type="dataset")
    else:
        raise SystemExit(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
