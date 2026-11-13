# Resuming the v3 studies on another machine

State as of 2026-11-13. This note is for picking the Granite v1 and Qwen v3 studies up on a Mac Studio (Apple Silicon). The design is in `docs/STUDY.md`. The protocol decisions are ADR-0022 to ADR-0024.

## Where things stand

| Study | Config | Search | Generate (before relaunch) | Judge |
|---|---|---|---|---|
| Qwen v3 | `experiments/study-qwen15-v3.yaml` | 60/60 | 3230/6000 | not started |
| Granite v1 | `experiments/study-granite41-v1.yaml` | 60/60 | 4255/4800 | not started |

The first jobs hit their time limits during generation. Nobody has seen an outcome label yet.

On 2026-11-13 one resume job per study was launched at the frozen commit `02b1f9d02f46db882d3f37d344f12feb7d773afd` (l40sx1):

- Qwen v3: job `6af6cbd75f2f1b7284b970d7`, 5h limit
- Granite v1: job `6af6cbd75f2f1b7284b970d9`, 4h limit

Each job restores the synced evidence and skips finished cells. It then finishes generate, runs judge, draws the audit sample, and runs a first analyze. The audit criterion stays `pending` until two people label the sheets.

## Why generate and judge stay on the GPU host

Both frozen plans pin the Linux CUDA environment, including the `nvidia-*`, `cuda-*` and `triton` packages. On macOS, `frozen()` refuses every evidence stage with "environment changed after freeze". Editing that check changes the code digest, and then every stage refuses with "initialize a new study". This is intended: a confirmatory study must not mix implementations. Only the analysis stages (`audit-score`, `analyze`) run on the Mac. They check only numpy and scipy.

If a job dies again, relaunch the same command. It resumes:

```sh
export HF_TOKEN=...                       # or: export HF_TOKEN=$(python -c "from huggingface_hub import get_token; print(get_token())")
export LASA_EVIDENCE_REPO=antonellof/lasa-evidence
C=02b1f9d02f46db882d3f37d344f12feb7d773afd
scripts/launch_hf_job.sh run experiments/study-qwen15-v3.yaml    $C l40sx1 5h
scripts/launch_hf_job.sh run experiments/study-granite41-v1.yaml $C l40sx1 4h
hf jobs ps -a                             # status; COMPLETED means ready to pull
```

## Raw evidence never goes into git

Suffixes and completions live in two places: the private dataset `antonellof/lasa-evidence`, and the gitignored `reports/` folder. The GitHub repository is public. Do not copy `reports/` into it, and do not attach those files anywhere else. Delete the dataset only after both studies are pulled and analyzed.

## Set up the Mac Studio

```sh
git clone https://github.com/antonellof/lineage-attack-surface-analyzer.git lasa && cd lasa
git checkout feat/study-v3
python3.11 -m venv .venv && . .venv/bin/activate
grep -vE '^(nvidia|cuda|triton)' requirements-study.txt > /tmp/requirements-mac.txt
pip install -r /tmp/requirements-mac.txt
pip install --no-deps nanogcg==0.3.0
pip install --no-deps -e .
hf auth login                             # account that owns the private dataset
```

`analyze` refuses unless numpy is 1.26.4 and scipy is 1.13.1. The summary lists every other package difference.

## After each job shows COMPLETED

```sh
export LASA_EVIDENCE_REPO=antonellof/lasa-evidence
python scripts/hf_evidence.py pull $LASA_EVIDENCE_REPO experiments/study-qwen15-v3.yaml
python scripts/hf_evidence.py pull $LASA_EVIDENCE_REPO experiments/study-granite41-v1.yaml
lasa study status --config experiments/study-qwen15-v3.yaml
lasa study status --config experiments/study-granite41-v1.yaml
```

The pull writes to `reports/<study_id>/` with owner-only permissions. It never overwrites an annotator sheet that already exists.

## Human audit and final analysis (per study)

Update, 2026-11-13: no human audit was run for the v3 paper. Two Claude models labeled each sample instead (ADR-0025), on sheets `human_labels_3.csv` and `human_labels_4.csv`, scored with `--labeler-kind model`. The frozen criterion stays `pending`. The steps below still apply if a human audit is run later.

1. Two annotators each label the 120 sampled outputs. Annotator 1 uses `human_labels.csv`, annotator 2 uses `human_labels_2.csv`, both in `reports/<study_id>/`. They work independently.
2. Score both sheets:

   ```sh
   lasa study audit-score --config <config> --labeler <name 1> --labeler-kind human
   lasa study audit-score --config <config> --labeler <name 2> --labeler-kind human --sheet human_labels_2.csv
   ```

3. Re-run the analysis so the frozen audit criterion (ADR-0021) is applied:

   ```sh
   lasa study analyze --config <config>
   ```

   The result is `reports/<study_id>/summary.json`. Report what it says. Do not change a frozen threshold or rule after seeing it.

4. When both studies are done: `python scripts/hf_evidence.py delete $LASA_EVIDENCE_REPO`.

Any finding about a public model goes to its owner before publication (ADR-0005, `docs/disclosure/`).
