"""
Smoke test for mut_model - verifies model can compile, forward/backward pass, and train.
Run from model_run/ directory:  python test_smoke.py
"""
import os, sys
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ['CUDA_VISIBLE_DEVICES'] = '0'

import numpy as np
# Monkey-patch: JAX 0.7.x uses np.asarray(copy=...) which needs numpy >= 2.0
# Our env has numpy 1.26.4, so we forward-port the API
_np_asarray_orig = np.asarray
def _np_asarray_patched(a, dtype=None, order=None, *, like=None, copy=None, device=None):
    return _np_asarray_orig(a, dtype=dtype, order=order)
np.asarray = _np_asarray_patched

import jax
import jax.numpy as jnp
import pandas as pd
from flax import nnx
from flax.nnx import bridge
import optax
import grain.python as grain
from scipy.stats import spearmanr

# Add parent to path for models import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models.model_direct import get_model_att_with_sequence_logits
from models.dataloder_direct_pretrain import MySource_seqs
from models.dataloder_direct import MySource_seqs as MySource_seqs_direct
from models.cv_dataset import create_cv_datasets
from models.save_fig import evaluate_with_recall, evaluate_with_spearman

TESTS_PASSED = 0
TESTS_FAILED = 0

def check(name, condition, detail=""):
    global TESTS_PASSED, TESTS_FAILED
    if condition:
        TESTS_PASSED += 1
        print(f"  ✅ {name}")
    else:
        TESTS_FAILED += 1
        print(f"  ❌ {name}  {detail}")

def run():
    print("=" * 60)
    print("mut_model Smoke Test")
    print("=" * 60)
    device = jax.devices()[0]
    print(f"Device: {device}")
    print(f"JAX: {jax.__version__}, Flax: {jax.__version__}")

    # ── 1. DATA LOADING ──────────────────────────────────────────
    print("\n── 1. Data Loading ──")
    table_path = "../data/test_protein-pretrain.csv"
    y_names = ['saprot_fitness', 'prosst_fitness', 'esmif_fitness']

    try:
        source_single = MySource_seqs(table_path, y_names[0])
        check("Single-y data source created", len(source_single) > 0, f"len={len(source_single)}")
    except Exception as e:
        check(f"Single-y data source", False, str(e))

    try:
        source_multi = MySource_seqs(table_path, y_names)
        check("Multi-y data source created", len(source_multi) > 0, f"len={len(source_multi)}")
    except Exception as e:
        check(f"Multi-y data source", False, str(e))

    # Test __getitem__
    try:
        item = source_single[0]
        check("Single-y __getitem__", 
              'sequence_onehot' in item and 'y' in item,
              f"keys={list(item.keys())}")
        check("Onehot shape", item['sequence_onehot'].shape[1] == 20,
              f"shape={item['sequence_onehot'].shape}")
    except Exception as e:
        check("Single-y __getitem__", False, str(e))

    try:
        item = source_multi[0]
        check("Multi-y __getitem__",
              'y_list' in item and len(item['y_list']) == 3,
              f"y_list_len={len(item.get('y_list', []))}")
    except Exception as e:
        check("Multi-y __getitem__", False, str(e))

    # ── 2. MODEL INSTANTIATION ───────────────────────────────────
    print("\n── 2. Model Instantiation ──")
    seq_len = len(source_single[0]['sequence'])
    vocab_size = 20
    batch_size = min(16, len(source_single))
    print(f"  seq_len={seq_len}, batch_size={batch_size}")

    try:
        key1 = jax.random.PRNGKey(42)
        X1 = jax.random.randint(key1, (batch_size, seq_len, vocab_size), 0, 1)
        
        model_raw = get_model_att_with_sequence_logits(
            sequence_length=seq_len,
            vocab_size=vocab_size,
            task_type='both',
            use_temperature=True,
            use_sequence_embedding=True,
            embed_dim=512
        )
        model = bridge.ToNNX(model_raw, rngs=nnx.Rngs(42))
        model = bridge.lazy_init(model, X1)
        check("Model instantiated via bridge.ToNNX", True)
        
        # Check model structure
        n_params = sum(p.size for p in jax.tree_util.tree_leaves(nnx.split(model)[1]))
        check(f"Model has parameters", n_params > 0, f"n_params={n_params:,}")
    except Exception as e:
        check("Model instantiation", False, str(e))
        return

    # ── 3. FORWARD PASS ──────────────────────────────────────────
    print("\n── 3. Forward Pass ──")
    try:
        x = jnp.array(source_single[0]['sequence_onehot']).reshape(1, seq_len, vocab_size)
        out = model(x)
        check("Forward pass (both)", 'global' in out and 'sequence' in out,
              f"keys={list(out.keys())}")
        check("Global output shape", out['global'].shape == (1, 1),
              f"shape={out['global'].shape}")
        check("Sequence output shape", out['sequence'].shape[0:2] == (1, seq_len),
              f"shape={out['sequence'].shape}")
    except Exception as e:
        check("Forward pass", False, str(e))
        return

    # Test per-output-type
    try:
        out_g = model(x, output_type='global')
        check("output_type='global'", out_g.shape == (1, 1), f"shape={out_g.shape}")
        out_s = model(x, output_type='sequence')
        check("output_type='sequence'", out_s.shape[0:2] == (1, seq_len), f"shape={out_s.shape}")
    except Exception as e:
        check("Per-output-type forward", False, str(e))

    # ── 4. GRADIENT COMPUTATION ──────────────────────────────────
    print("\n── 4. Gradient Computation ──")
    try:
        y_target = jnp.array([[0.5]])
        def loss_fn(m):
            pred = m(x)['global']
            return jnp.mean((y_target - pred) ** 2)
        
        loss_val = loss_fn(model)
        check("Loss computed", jnp.isfinite(loss_val), f"loss={float(loss_val):.6f}")
        
        grads = nnx.grad(loss_fn)(model)
        n_grad_finite = sum(1 for g in jax.tree_util.tree_leaves(grads) 
                           if jnp.all(jnp.isfinite(g)))
        n_grad_total = sum(1 for _ in jax.tree_util.tree_leaves(grads))
        check("Gradients are finite", n_grad_finite == n_grad_total,
              f"{n_grad_finite}/{n_grad_total} finite")
    except Exception as e:
        check("Gradient computation", False, str(e))
        return

    # ── 5. OPTIMIZER + TRAINING STEP ─────────────────────────────
    print("\n── 5. Optimizer + Training Step ──")
    try:
        optimizer = nnx.Optimizer(model, optax.adam(1e-4))
        
        @nnx.jit
        def train_step(m, opt, x_in, y_in):
            def l_fn(m2):
                return jnp.mean((y_in - m2(x_in)['global']) ** 2)
            g = nnx.grad(l_fn)(m)
            opt.update(g)
        
        x_batch = jnp.array(source_single[0]['sequence_onehot']).reshape(1, seq_len, vocab_size)
        y_batch = jnp.array(source_single[0]['y'])
        
        train_step(model, optimizer, x_batch, y_batch)
        check("Single train_step executed", True)
        
        # Multi-step to verify optimizer is moving weights
        loss_before = float(loss_fn(model))
        for _ in range(5):
            train_step(model, optimizer, x_batch, y_batch)
        loss_after = float(loss_fn(model))
        check("Loss changes after training steps", 
              loss_after != loss_before,
              f"before={loss_before:.6f}, after={loss_after:.6f}")
    except Exception as e:
        check("Training step", False, str(e))
        return

    # ── 6. MINI BATCH TRAINING LOOP ──────────────────────────────
    print("\n── 6. Mini Batch Training (2 epochs) ──")
    try:
        # Re-init fresh model
        model2 = bridge.ToNNX(get_model_att_with_sequence_logits(
            sequence_length=seq_len, vocab_size=vocab_size,
            task_type='both', use_temperature=True,
            use_sequence_embedding=True, embed_dim=512
        ), rngs=nnx.Rngs(42))
        model2 = bridge.lazy_init(model2, X1)
        optimizer2 = nnx.Optimizer(model2, optax.adam(1e-3))
        
        train_ds = (grain.MapDataset.source(source_single)
                    .shuffle(seed=42)
                    .slice(slice(0, int(len(source_single)*0.8)))
                    .batch(batch_size=4))

        losses = []
        for epoch in range(2):
            epoch_loss = 0.0
            n_steps = 0
            for data in train_ds:
                x_i = data['sequence_onehot']
                y_i = data['y']
                train_step(model2, optimizer2, x_i, y_i)
                pred = model2(x_i)['global']
                loss = float(jnp.mean((y_i - pred) ** 2))
                epoch_loss += loss
                n_steps += 1
            avg_loss = epoch_loss / max(n_steps, 1)
            losses.append(avg_loss)
            print(f"  Epoch {epoch+1}: avg_loss={avg_loss:.6f}")
        
        check("Batch training loop runs", len(losses) == 2,
              f"losses={[f'{l:.4f}' for l in losses]}")
        check("Loss decreases or is stable", losses[-1] < losses[0] * 1.5,
              f"epoch1={losses[0]:.4f}, epoch2={losses[1]:.4f}")
    except Exception as e:
        check("Batch training loop", False, str(e))
        return

    # ── 7. CV DATASET ────────────────────────────────────────────
    print("\n── 7. CV Dataset Creation ──")
    try:
        finetune_table = "../data/test_protein.csv"
        source_ft = MySource_seqs_direct(finetune_table, 'DMS_score')
        cv_ds = create_cv_datasets(source_ft, cv_scheme='fold_random_5', seed=42,
                                   batch_size_train=4, batch_size_test=8)
        check("CV datasets created", len(cv_ds) == 5, f"n_folds={len(cv_ds)}")
        check("Each fold has train/test", 
              all('train_dataset' in f and 'test_dataset' in f for f in cv_ds))
    except Exception as e:
        check("CV dataset creation", False, str(e))

    # ── 8. EVALUATION METRICS ────────────────────────────────────
    print("\n── 8. Evaluation Metrics ──")
    try:
        model3 = bridge.ToNNX(get_model_att_with_sequence_logits(
            sequence_length=seq_len, vocab_size=vocab_size,
            task_type='both', use_temperature=True,
            use_sequence_embedding=True, embed_dim=512
        ), rngs=nnx.Rngs(42))
        model3 = bridge.lazy_init(model3, X1)

        test_ds = (grain.MapDataset.source(source_ft)
                   .batch(batch_size=8))
        
        recall = evaluate_with_recall(model3, test_ds, threshold_percentiles=[70, 90])
        check("Recall evaluation runs", len(recall) >= 1, f"n_thresholds={len(recall)}")
        
        corr, mse = evaluate_with_spearman(model3, test_ds)
        check("Spearman evaluation runs", corr is not None)
    except Exception as e:
        check("Evaluation metrics", False, str(e))

    # ── 9. MODEL SAVE/LOAD (pickle) ──────────────────────────────
    print("\n── 9. Model Serialization ──")
    try:
        import pickle, tempfile
        graph, state = nnx.split(model3)
        with tempfile.NamedTemporaryFile(suffix='.pkl', delete=True) as f:
            pickle.dump(state, f)
            f.flush()
            f.seek(0)
            loaded_state = pickle.load(f)
        model_loaded = nnx.merge(graph, loaded_state)
        out_before = model3(x)['global']
        out_after = model_loaded(x)['global']
        check("Pickle roundtrip", jnp.allclose(out_before, out_after))
    except Exception as e:
        check("Model serialization", False, str(e))

    # ── SUMMARY ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    total = TESTS_PASSED + TESTS_FAILED
    print(f"Results: {TESTS_PASSED}/{total} passed, {TESTS_FAILED} failed")
    if TESTS_FAILED == 0:
        print("✅ mut_model is WORKING correctly!")
    else:
        print(f"❌ {TESTS_FAILED} test(s) failed - model has issues.")
    print("=" * 60)
    return TESTS_FAILED == 0

if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)
