"""Validation contracts for the prospective study; no model or network access."""
from __future__ import annotations

import jsonschema

MODEL = {"type": "object", "required": ["model_id"], "properties": {
    "model_id": {"type": "string", "minLength": 1},
    "path": {"type": "string", "minLength": 1},
    "revision": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
    "evaluation_role": {"enum": ["primary", "task_control"]},
}}
SCHEMA = {
    "type": "object",
    "required": ["study_id", "seed", "protocol_status", "authorization", "output_dir", "base",
                 "variants", "independent_judge", "benchmark", "gcg", "generations", "max_new_tokens"],
    "properties": {
        "study_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]*$"},
        "seed": {"type": "integer", "minimum": 0, "maximum": 4294967295},
        "protocol_status": {"enum": ["pilot", "confirmatory"]},
        "authorization": {"type": "string", "minLength": 1},
        "output_dir": {"type": "string", "minLength": 1},
        "base": MODEL,
        "variants": {"type": "array", "minItems": 1, "items": MODEL},
        "independent_judge": {"allOf": [MODEL, {
            "required": ["rubric"], "properties": {"rubric": {"type": "string", "minLength": 1}}}]},
        "benchmark": {"type": "object", "required": ["name", "per_category"], "properties": {
            "name": {"type": "string", "minLength": 1},
            "per_category": {"type": "integer", "minimum": 2, "multipleOf": 2},
            "categories": {"type": "array", "minItems": 1, "uniqueItems": True,
                           "items": {"type": "string", "minLength": 1}},
            "targets": {"enum": ["generic", "published"]},
        }},
        "controls": {"type": "object", "additionalProperties": False, "properties": {
            "mismatched_suffix": {"type": "boolean"},
        }},
        "decision": {"type": "object", "additionalProperties": False,
                     "required": ["alpha", "max_dev_bare_rate", "n_permutations",
                                  "primary_controls", "tie_preference"], "properties": {
            "alpha": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
            "max_dev_bare_rate": {"type": "number", "minimum": 0, "maximum": 1},
            "n_permutations": {"type": "integer", "minimum": 1000},
            "primary_controls": {"type": "array", "minItems": 1, "uniqueItems": True,
                                 "items": {"enum": ["bare", "random_suffix", "mismatched_gcg"]}},
            "tie_preference": {"enum": ["source_uplift", "loss_proxy"]},
            "audit": {"type": "object", "additionalProperties": False,
                      "required": ["min_precision", "max_false_omission_rate", "min_inter_annotator_agreement"],
                      "properties": {
                          "min_precision": {"type": "number", "minimum": 0, "maximum": 1},
                          "max_false_omission_rate": {"type": "number", "minimum": 0, "maximum": 1},
                          "min_inter_annotator_agreement": {"type": "number", "minimum": 0, "maximum": 1}}},
        }},
        "human_audit_design": {"enum": ["simple_random", "stratified_classifier_label"]},
        "human_audit_positive_share": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
        "human_annotators": {"type": "integer", "minimum": 1, "maximum": 5},
        "commitment_dir": {"type": "string", "minLength": 1},
        "gcg": {"type": "object", "required": ["num_steps", "search_width", "topk"],
                "properties": {k: {"type": "integer", "minimum": 1}
                               for k in ("num_steps", "search_width", "topk", "batch_size")}},
        "generations": {"type": "integer", "minimum": 2},
        "batch_replicates": {"type": "boolean"},
        "torch_dtype": {"enum": ["float16", "bfloat16", "float32"]},
        "max_new_tokens": {"type": "integer", "minimum": 1},
        "human_audit_n": {"type": "integer", "minimum": 1},
        "top_k": {"type": "integer", "minimum": 1},
    },
}


def validate_config(cfg):
    try:
        jsonschema.validate(cfg, SCHEMA)
    except jsonschema.ValidationError as exc:
        raise ValueError(f"invalid study config: {exc.message}") from exc
    decision = cfg.get("decision")
    if decision and "mismatched_gcg" in decision["primary_controls"] and not cfg.get("controls", {}).get("mismatched_suffix"):
        raise ValueError("invalid study config: mismatched_gcg is a primary control but the control is disabled")


def generation_key(row):
    return (row["model_id"], row["behavior_id"], row["condition"], row["replicate"])


def validate_evidence(evidence, plan, model_ids, require_complete=False):
    """Validate the Cartesian design, IDs and split labels before using outcomes."""
    import hashlib
    import json

    behaviors = {b["behavior_id"]: b for b in plan["behaviors"]}
    expected = {(m, b, c, r) for m in model_ids for b in behaviors
                for c in plan["conditions"] for r in range(plan["config"]["generations"])}
    seen = set()
    for row in evidence["items"]:
        key = generation_key(row)
        if key not in expected or key in seen:
            raise ValueError("unexpected or duplicate generation key")
        seen.add(key)
        identifier = hashlib.sha256(json.dumps(list(key), sort_keys=True).encode()).hexdigest()[:24]
        if row["id"] != identifier:
            raise ValueError("generation ID does not match its experimental key")
        b = behaviors[row["behavior_id"]]
        if row["split"] != b["split"] or row["category"] != b["category"]:
            raise ValueError("generation split/category does not match frozen benchmark")
    if require_complete and seen != expected:
        raise ValueError("generation evidence is incomplete")
    return seen
