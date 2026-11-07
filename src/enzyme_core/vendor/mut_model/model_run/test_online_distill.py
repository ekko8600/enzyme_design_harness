"""Unit and smoke tests for the standalone online-distillation entrypoint."""

import tempfile
import unittest
from pathlib import Path

import jax
import jax.numpy as jnp
import numpy as np

from model_run.finetune_online_distill import (
    EmpiricalCDFNormalizer,
    FitnessTransformer,
    TeacherCache,
    acquire_indices,
    create_state,
    encode_sequences,
    make_dms_split,
    ranking_metrics,
    stratified_random_indices,
    train_step,
)


TRAINING_CONFIG = {
    "learning_rate": 3e-4,
    "encoder_lr_scale": 0.1,
    "weight_decay": 1e-4,
}


class OnlineDistillationTests(unittest.TestCase):
    def test_training_only_ecdf_does_not_refit_on_test(self):
        normalizer = EmpiricalCDFNormalizer([0.0, 1.0, 2.0, 3.0])
        transformed = normalizer.transform([-100.0, 1.5, 100.0, np.nan])
        np.testing.assert_allclose(transformed[:3], [0.0, 0.5, 1.0])
        self.assertTrue(np.isnan(transformed[-1]))
        self.assertEqual(len(normalizer.sorted_values), 4)

    def test_dms_split_is_disjoint_and_reproducible(self):
        first = make_dms_split(100, 20, 10, 42)
        second = make_dms_split(100, 20, 10, 42)
        np.testing.assert_array_equal(first.train_indices, second.train_indices)
        self.assertFalse(set(first.train_indices) & set(first.val_indices))
        self.assertFalse(set(first.train_indices) & set(first.test_indices))
        self.assertFalse(set(first.val_indices) & set(first.test_indices))
        self.assertEqual(len(np.concatenate([first.train_indices, first.val_indices, first.test_indices])), 100)

    def test_teacher_cache_is_versioned(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = TeacherCache(Path(directory) / "scores.sqlite")
            cache.put_many(["AAAA"], "teacher", "v1", [0.7], ["ok"], 0.1)
            self.assertEqual(cache.get("AAAA", "teacher", "v1"), (0.7, "ok"))
            self.assertIsNone(cache.get("AAAA", "teacher", "v2"))
            cache.close()

    def test_stratified_sampling_is_unique(self):
        available = np.arange(12)
        counts = np.repeat([1, 2, 3], 4)
        selected = stratified_random_indices(available, counts, 9, np.random.default_rng(1))
        self.assertEqual(len(selected), 9)
        self.assertEqual(len(set(selected.tolist())), 9)
        self.assertEqual(set(counts[selected].tolist()), {1, 2, 3})

    def test_acquisition_respects_budget_and_uniqueness(self):
        rng = np.random.default_rng(7)
        available = np.arange(20)
        queried = np.arange(20, 24)
        tokens = rng.integers(0, 20, size=(24, 8), dtype=np.int32)
        means = np.linspace(0.0, 1.0, 24)
        stds = np.linspace(1.0, 0.0, 24)
        counts = np.ones(24, dtype=np.int32)
        selected, reasons = acquire_indices(
            available, queried, means, stds, tokens, counts, 10,
            {
                "screen_size": 20, "exploit_fraction": 0.4,
                "uncertainty_fraction": 0.3, "diversity_fraction": 0.2,
                "random_fraction": 0.1, "ucb_beta": 1.0,
            },
            rng,
        )
        self.assertEqual(len(selected), 10)
        self.assertEqual(len(set(selected.tolist())), 10)
        self.assertTrue(set(selected.tolist()).issubset(set(available.tolist())))
        self.assertEqual(set(selected.tolist()), set(reasons))

    def test_ranking_metrics_are_one_for_perfect_ranking(self):
        values = np.arange(20, dtype=float)
        metrics = ranking_metrics(values, values)
        self.assertAlmostEqual(metrics["spearman"], 1.0)
        self.assertAlmostEqual(metrics["recall_top10"], 1.0)
        self.assertAlmostEqual(metrics["ndcg_top10"], 1.0)

    def test_model_forward_and_training_step(self):
        sequences = ["ACDE", "ACDF", "ACDG", "ACDH"]
        tokens, mask = encode_sequences(sequences, 4)
        model = FitnessTransformer(
            num_teachers=2, max_length=4, embed_dim=16,
            num_heads=2, num_layers=1, mlp_dim=32, dropout=0.0,
        )
        state = create_state(jax.random.PRNGKey(0), model, 4, TRAINING_CONFIG)
        before = state.params
        state, metrics = train_step(
            state,
            {
                "tokens": jnp.asarray(tokens), "mask": jnp.asarray(mask),
                "teacher_targets": jnp.asarray([[0.1, 0.2], [0.3, 0.4], [0.7, 0.6], [0.9, 0.8]]),
            },
            {
                "tokens": jnp.asarray(tokens), "mask": jnp.asarray(mask),
                "target": jnp.asarray([0.1, 0.3, 0.7, 0.9]),
            },
            jnp.asarray([0.5, 0.5]), jnp.asarray([0.35, 0.35, 0.2, 0.1]),
            0.05, 0.1, 2.0, 1.0, jax.random.PRNGKey(1), False,
        )
        self.assertTrue(np.isfinite(float(metrics["total_loss"])))
        changed = [
            not np.allclose(np.asarray(left), np.asarray(right))
            for left, right in zip(jax.tree_util.tree_leaves(before), jax.tree_util.tree_leaves(state.params))
        ]
        self.assertTrue(any(changed))


if __name__ == "__main__":
    unittest.main()
