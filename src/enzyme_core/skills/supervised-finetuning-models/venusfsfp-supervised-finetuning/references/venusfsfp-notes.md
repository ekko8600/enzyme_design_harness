# VenusFSFP Local Notes

Use these notes before patching or tuning the local VenusFSFP project.

## Known Local Project

The reference project must resolve from `the resource configuration selected by ENZYME_CORE_RESOURCES (default: runtime/resources.json)` as
`$VENUSFSFP_HOME`. The user may
refer to it as `/data/VenusFSFP`; verify both paths.

The project currently supports local ESM-2 and SaProt model paths in
`fsfp/config.json`:

- `esm2`: `$VENUSFSFP_ESM2_BASE`
- `saprot`: resolve from `model_weights.models.saprot` as `$SAPROT_CHECKPOINT`

Use ESM-2 first unless the target has a compatible SaProt structure sequence in
`data/struc_sq_72.csv`.

## Data Format

Raw data files live in `data/substitutions/*.csv`.

Required columns:

- `mutant`: mutation string, 1-indexed, e.g. `M1A` or `A23V:G57D`
- `mutated_sequence`: full mutant sequence
- `DMS_score`: numeric target value
- `DMS_score_bin`: binary class used for ranking summaries
- `GEMME`: optional augmentation column read when `--augment GEMME` is used

The filename matters. `merge_files()` groups proteins with:

```python
name = "_".join(file_name.split("_")[:2])
```

Always name custom files with at least two underscore-separated parts, e.g.
`TARGET_custom.csv`. Use `--protein TARGET` for VenusFSFP CLI selection.

For ESM-2 production inference, use the skill's
`scripts/score_checkpoint.py`. Pass the saved full-data meta-transfer checkpoint
explicitly; the script performs inference only and does not need dummy labels or
measured rows prepended to the candidate table. Do not use
`main.py --test --epochs 0`, because that path loads the base model rather than
the saved production adapter.

## Baseline Commands

From the VenusFSFP run directory:

```bash
export PYTHON="$CONDA_PREFIX/bin/python"
$PYTHON preprocess.py
$PYTHON retrieve.py -m vectorize -md esm2 -b 8
$PYTHON retrieve.py -m retrieve -md esm2 -b 16 -k 1 -mt cosine -cpu
```

Then train:

```bash
$PYTHON main.py --mode meta --model esm2 --protein TARGET \
  --train_size 40 --train_batch 1 --lora_r 16 --learning_rate 1e-4 \
  --epochs 30 --patience 10 --list_size 10 --max_iter 5 \
  --retr_metric cosine --augment GEMME --meta_tasks 1 \
  --meta_train_batch 16 --meta_eval_batch 128 --adapt_lr 5e-3 \
  --adapt_steps 5 --cross_validation 0

$PYTHON main.py --mode meta-transfer --model esm2 --protein TARGET \
  --train_size 40 --train_batch 16 --lora_r 16 --learning_rate 1e-4 \
  --epochs 30 --patience 10 --list_size 10 --max_iter 5 \
  --retr_metric cosine --augment GEMME --meta_tasks 1 \
  --meta_train_batch 16 --meta_eval_batch 128 --adapt_lr 5e-3 \
  --adapt_steps 5 --cross_validation 0
```

Use the original `run.sh` defaults for full production benchmarking:

- `epochs 100`
- `patience 15`
- `meta_tasks 3`
- `retrieve -k 71`
- `eval_batch 52000`

Adjust `retrieve -k` to `number_of_proteins - 1`. The local two-protein smoke
test used `-k 1`.

## Local Compatibility Fixes Already Used

Python 3.12 and PyTorch 2.6+ can break the upstream project. The successful
local run used these fixes:

- Lazy-load `learn2learn.algorithms.MAML` inside `MetaRankingTrainer.__init__`
  instead of importing it at module top level.
- Rebuild `learn2learn` Cython extensions if the installed package references
  Python internals removed in Python 3.12.
- Add `weights_only=False` to `torch.load()` calls that load VenusFSFP pickle
  artifacts:
  - `main.py` loading `data/merged.pkl`
  - `fsfp/pipeline.py` loading checkpoint `logs.pkl`
  - `fsfp/pipeline.py` loading `retrieved/topk_*.pkl`
  - `retrieve.py` loading `data/merged.pkl`
  - `fsfp/retrieval.py` loading `retrieved/vectors_*.pkl`
- Use local Hugging Face snapshots when network access to the Hub times out.

## Observed Smoke-Test Results

On 2026-06-14 in conda env `agent`, RTX 5060 Ti 16 GB:

- ESM-2 meta-transfer on `SYUA_HUMAN`, 40-shot, `GEMME`, `meta_tasks 1`:
  `all_rest` Spearmanr about `0.552`.
- ESM-2 finetune under similar conditions: `all_rest` Spearmanr about `0.496`.
- SaProt finetune: `all_rest` Spearmanr about `0.502`.
- SaProt meta-transfer under the two-protein setup was worse than finetune,
  likely because there was only one auxiliary protein and the structure-token
  vocabulary is sparse.

Treat these as local sanity checks, not universal expected performance.

## Large-Library Pre-training Strategy

Always run the large-library pseudo-label pretraining workflow before
meta-transfer. Prefer a zero-shot model (SaProt, GEMME, ESM-IF, etc.) with
Spearman r > 0.5 against experimental scores. If no teacher is scientifically
usable, stop and report the run as blocked rather than directly fine-tuning:

1. Generate single-site saturation (L × 19 mutants, where L = sequence length)
   plus 30,000 random 2-3 site mutations using
   `mut_model_tools.py generate-library --max-sites 3 --random-multisite 30000`.
2. Score the full library with the highest-correlating zero-shot model.
3. Build a VenusFSFP CSV with zero-shot scores as `DMS_score` and as the
   `--augment` column. File name: `TARGET_pretrain_assay.csv`.
4. Build a separate CSV with real experimental labels:
   `TARGET_finetune_assay.csv`.
5. Run `preprocess.py` and `retrieve.py` once (both CSV files loaded into the
   same merged data).
6. Meta-training on `TARGET_pretrain` with full data:
   `--train_size <library_count> --cross_validation 0 --n_sites 0`.
7. Meta-transfer on `TARGET_finetune` with real labels:
   `--train_size <real_count> --cross_validation 0`. Use every valid
   experimental row, save the final production checkpoint, and use only that
   checkpoint for large-library prediction.

The `_ms` path suffix is added by VenusFSFP when `--n_sites` is not `[1]`.
When meta-training uses `--n_sites 0` (converted to `None`), the checkpoint
path includes `_ms`. Meta-transfer also includes `_ms`, so they should match.
If a mismatch occurs, create a symlink:
`ln -s <actual_dir> <actual_dir>_ms`

## Augmentation Model Selection

`--augment` reads any column from the raw CSV. The column name must match the
`--augment` value exactly. Run Spearman correlation analysis to choose the
best zero-shot model as the augment source:

```python
from scipy.stats import spearmanr
exp_scores = [row['activity'] for row in measured]  # experimental
for plm_col in ['saprot_fitness', 'esmif_fitness', 'GEMME', ...]:
    r, p = spearmanr(exp_scores, [row[plm_col] for row in measured])
    print(f'{plm_col}: r={r:.4f}')
```

Choose the column with the highest r. When GEMME is selected, it requires the
MSA pipeline (protein-sequence-msa + gemme skills).

## Observed Test2 Results (2026-07-08)

On a custom enzyme (194 AA, 124 mutants), using `saprot_fitness` as augment:

| Target | Zero-shot SaProt r | Best meta r | Best meta-transfer r |
|--------|--------------------|-------------|----------------------|
| active | 0.683 | 0.719 | 0.719 |
| stab   | 0.660 | 0.668 | 0.610 |

Active benefited more from fine-tuning (gap +0.036). Stab showed marginal
improvement (gap +0.008), likely because most stab values are 0.0.

Compared to GEMME (genuine evolutionary scores), SaProt zero-shot showed
higher correlation for this protein. GEMME column was generated but not used
as the primary augment for these runs (still available in the CSV for
experimentation).
