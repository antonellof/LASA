#!/bin/sh
# Launch one study stage as a Hugging Face Job (ADR-0022).
#   scripts/launch_hf_job.sh <calibrate|init|run> <config> <commit> [flavor] [timeout]
# The commit must be on the public remote: the job clones it, and a confirmatory
# search checks that a remote branch contains the plan commitment.
# Needs HF_TOKEN in the environment and LASA_EVIDENCE_REPO (a private dataset id).
set -eu
stage=$1; config=$2; commit=$3; flavor=${4:-a10g-small}; timeout=${5:-6h}
: "${HF_TOKEN:?set HF_TOKEN}" "${LASA_EVIDENCE_REPO:?set LASA_EVIDENCE_REPO to a private dataset id}"
url=$(git remote get-url origin)
git branch -r --contains "$commit" | grep -q . || { echo "stop: $commit is not on a remote branch"; exit 4; }
exec hf jobs run --detach --flavor "$flavor" --timeout "$timeout" --secrets HF_TOKEN \
  --label "lasa=$stage" python:3.11-bookworm bash -c "set -eu
git clone -q $url /work/lasa && cd /work/lasa && git checkout -q $commit
pip install -q --no-cache-dir -r requirements-study.txt
pip install -q --no-cache-dir --no-deps nanogcg==0.3.0
pip install -q --no-cache-dir --no-deps -e .
python scripts/hf_job.py --stage $stage --config $config --evidence-repo $LASA_EVIDENCE_REPO"
