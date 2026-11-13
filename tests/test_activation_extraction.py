"""Padding regression tests use tiny random models and benign prompts only."""

import numpy as np
import pytest
import torch
from transformers import BatchEncoding, LlamaConfig, LlamaForCausalLM, GPT2Config, GPT2LMHeadModel

from lasa.attacks.refusal_direction import (
    collect_last_token_activations,
    last_attended_indices,
)


@pytest.mark.parametrize("mask, expected", [
    ([[1, 1, 0, 0], [1, 1, 1, 1]], [1, 3]),
    ([[0, 0, 1, 1], [1, 1, 1, 1]], [3, 3]),
    ([[0, 1, 0, 1]], [3]),
])
def test_last_attended_position(mask, expected):
    assert last_attended_indices(torch.tensor(mask)).tolist() == expected


def test_empty_sequence_is_rejected():
    with pytest.raises(ValueError, match="attended token"):
        last_attended_indices(torch.tensor([[0, 0]]))


class BenignTokenizer:
    chat_template = None

    def __init__(self, side):
        self.side = side

    def __call__(self, prompts, **kwargs):
        # Character encoding makes prompt lengths differ without external assets.
        ids = [[3 + ord(c) % 50 for c in p] for p in prompts]
        width = max(map(len, ids))
        rows, masks = [], []
        for row in ids:
            n = width - len(row)
            if self.side == "left":
                rows.append([0] * n + row)
                masks.append([0] * n + [1] * len(row))
            else:
                rows.append(row + [0] * n)
                masks.append([1] * len(row) + [0] * n)
        return BatchEncoding({"input_ids": torch.tensor(rows), "attention_mask": torch.tensor(masks)})


@pytest.mark.parametrize("side", ["left", "right"])
@pytest.mark.parametrize("architecture", ["llama", "gpt2"])
def test_collection_matches_single_sequence_across_padding_and_batch_order(side, architecture):
    torch.manual_seed(7)
    if architecture == "llama":
        model = LlamaForCausalLM(LlamaConfig(
            vocab_size=64, hidden_size=16, intermediate_size=32,
            num_hidden_layers=2, num_attention_heads=2, num_key_value_heads=2,
            max_position_embeddings=256, pad_token_id=0,
            attn_implementation="eager",
        )).eval()
    else:
        model = GPT2LMHeadModel(GPT2Config(vocab_size=64, n_embd=16, n_layer=2,
                                         n_head=2, n_positions=256, pad_token_id=0)).eval()
    tok = BenignTokenizer(side)
    probes = ["Hello", "Describe a garden in spring.", "Tea"]
    single = collect_last_token_activations(model, tok, probes, batch_size=1)
    batched = collect_last_token_activations(model, tok, probes, batch_size=3)
    reversed_batch = collect_last_token_activations(model, tok, probes[::-1], batch_size=3)
    for one, many, reverse in zip(single, batched, reversed_batch):
        np.testing.assert_allclose(one, many, atol=1e-6, rtol=1e-5)
        np.testing.assert_allclose(one, reverse[::-1], atol=1e-6, rtol=1e-5)
