# mut_model Local Notes

Use these notes before editing the checkout resolved as `$MUT_MODEL_HOME`.

## Project Entry Points

Run from `$MUT_MODEL_HOME/model_run`:

- `pretrain.py`: pseudo-label pretraining.
- `finetune_SFT.py`: cross-validation supervised fine-tuning on `DMS_score`.
- `finetune_DPO_fewshot.py`: legacy ProteinGym few-shot DPO evaluation; not a production entrypoint.
- `test_smoke.py`: environment and model sanity test.

Read `finetuning-options.md` for the production SFT/DPO/both selection workflow.

`README.md` mentions `evaluate.py`, but this checkout does not contain it. Use
the skill's `scripts/mut_model_tools.py score-library` for large-library
scoring.

## File Naming Contract

`data/DMS_substitutions-test.csv` drives both scripts:

```csv
DMS_id
target_protein
```

For each `DMS_id`, the scripts expect:

- `data/<DMS_id>-pretrain.csv`
- `data/<DMS_id>.csv`

Pretrain columns:

- `mutated_sequence`
- one or more pseudo-label columns, currently defaulting in code to
  `saprot_fitness`, `prosst_fitness`, `esmif_fitness`

Finetune columns:

- `mutated_sequence`
- `DMS_score`
- an independent numeric teacher-reference column when DPO is selected

The dataloaders normalize every target column to `[-1, 1]`. Constant-valued
teacher columns are not useful and can break interpretation.

## Important Hardcoded Settings

`pretrain.py`:

- `os.environ["TRANSFORMERS_OFFLINE"] = "1"`
- `os.environ["CUDA_VISIBLE_DEVICES"] = "1"`
- `y_names = ['saprot_fitness', 'prosst_fitness', 'esmif_fitness']`
- `train_mode = 'mixed'`
- `epochs = 130`
- pretrain split: first 90% train, last 10% validation after shuffled dataset
- saves:
  - `pretrain-<DMS_id>-<first_y_name>.pkl` for `single`
  - `pretrain-<DMS_id>-mixed.pkl` for `mixed`
  - `pretrain-<DMS_id>-random.pkl` for `random`

`finetune_SFT.py`:

- `os.environ["CUDA_VISIBLE_DEVICES"] = "0"`
- `cv_schemes = ['fold_random_5','fold_modulo_5', 'fold_contiguous_5']`
- `y_name = 'DMS_score'`
- hardcoded pretrain load:
  `../pretrain_weight/pretrain-<DMS_id>-random.pkl`
- saves fold weights to:
  `../finetune_weight/finetune-<fold>-<DMS_id>-<cv_scheme>.pkl`
- writes metrics to:
  `../result/<DMS_id>-finetune-<cv_scheme>-random.json`

This is a CV-only implementation. It does not satisfy the skill's default
production stage, which must train on every valid experimental row without a
train/test split. Use the skill's `scripts/finetune_full.py` for production. It
saves `../finetune_weight/finetune-full-<DMS_id>-random.pkl` plus a JSON run
record. Keep the CV outputs diagnostic-only; never use them as the default
large-library scoring weight.


For production DPO, use the skill's `scripts/finetune_dpo_full.py`. It starts
from `pretrain-<DMS_id>-random.pkl`, builds preference pairs covering all
experimental rows, and saves `finetune-dpo-full-<DMS_id>-random.pkl`. It
requires an independent numeric teacher-reference column for every measured mutant.
Because finetuning expects `random.pkl`, use `train_mode = 'random'` in
pretraining unless the finetune load path is deliberately edited.

## Model Shape

The model is built with:

- `sequence_length = len(first mutated_sequence)`
- `vocab_size = 20`
- `task_type = 'both'`
- `use_temperature = True`
- `use_sequence_embedding = True`
- `embed_dim = 512`

All sequences scored by one weight file must have the same length as the
training target.

## Suggested Run Modes

Optional diagnostic, only when explicitly requested:

- set `cv_schemes = ['fold_random_5']`
- reduce `epochs` if only checking plumbing
- run `test_smoke.py` first

Required final production run:

- complete and verify target-specific pseudo-label pretraining
- let the user choose SFT, DPO, or both
- initialize each selected method independently from the same verified
  `pretrain-<DMS_id>-random.pkl`
- train each selected method on all valid experimental rows with no split
- save `finetune-full-<DMS_id>-random.pkl` for SFT and/or
  `finetune-dpo-full-<DMS_id>-random.pkl` for DPO
- score the large library separately with every selected production weight
