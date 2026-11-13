"""JSON Schemas for LASA inter-stage artifacts."""

from __future__ import annotations

import jsonschema

LOAD_SCHEMA = {
    "type": "object",
    "required": ["seed", "models"],
    "properties": {
        "seed": {"type": "integer"},
        "models": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["role", "model_id", "path", "digest", "files"],
            },
        },
    },
}

SIMILARITY_SCHEMA = {
    "type": "object",
    "required": ["seed", "n_probes", "metrics", "base", "variants"],
    "properties": {
        "seed": {"type": "integer"},
        "n_probes": {"type": "integer", "minimum": 2},
        "metrics": {"type": "array", "items": {"type": "string"}},
        "base": {"type": "string"},
        "variants": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["model_id", "profile"],
            },
        },
    },
}

ATTACK_BASE_SCHEMA = {
    "type": "object",
    "required": ["seed", "base", "benchmark", "tools", "attacks"],
    "properties": {
        "seed": {"type": "integer"},
        "base": {"type": "string"},
        "benchmark": {"type": "object"},
        "tools": {"type": "array"},
        "attacks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "attack_id",
                    "behavior_id",
                    "category",
                    "primitive",
                    "base_success",
                    "strength",
                ],
            },
        },
    },
}

PREDICT_SCHEMA = {
    "type": "object",
    "required": ["seed", "scoring", "similarity_metric", "base", "variants"],
    "properties": {
        "seed": {"type": "integer"},
        "scoring": {"type": "string"},
        "similarity_metric": {"type": "string"},
        "base": {"type": "string"},
        "variants": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["model_id", "predictions"],
            },
        },
    },
}

VERIFY_SCHEMA = {
    "type": "object",
    "required": [
        "seed",
        "authorization_id",
        "top_k",
        "generations_per_attack",
        "spotcheck_agreement",
        "variants",
    ],
    "properties": {
        "seed": {"type": "integer"},
        "authorization_id": {"type": "string"},
        "top_k": {"type": "integer"},
        "generations_per_attack": {"type": "integer"},
        "spotcheck_agreement": {
            "type": "object",
            "required": ["n_checked", "agreement", "cohens_kappa"],
        },
        "variants": {"type": "array"},
    },
}

MANIFEST_SCHEMA = {
    "type": "object",
    "required": [
        "run_id",
        "timestamp",
        "seed",
        "authorization_id",
        "authorization_basis",
        "models",
        "tool_versions",
    ],
}

STAGE_SCHEMAS = {
    "load": LOAD_SCHEMA,
    "similarity": SIMILARITY_SCHEMA,
    "attack_base": ATTACK_BASE_SCHEMA,
    "predict": PREDICT_SCHEMA,
    "verify": VERIFY_SCHEMA,
    "manifest": MANIFEST_SCHEMA,
}


def validate_artifact(name: str, payload: dict) -> None:
    if name in STAGE_SCHEMAS:
        jsonschema.validate(instance=payload, schema=STAGE_SCHEMAS[name])
