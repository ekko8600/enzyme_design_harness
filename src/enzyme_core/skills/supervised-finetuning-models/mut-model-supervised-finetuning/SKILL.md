---
name: mut-model-supervised-finetuning
description: 'Supervised fine-tuning model skill for the local mut_model JAX/Flax
  protein mutation predictor. Always build pseudo-labeled mutant data and complete
  pseudo-label pretraining before fine-tuning on real experimental DMS/fitness data;
  direct fine-tuning from an unverified or generic initialization is not allowed.
  By default, fine-tune once on all available experimental rows without a train/test
  split. Let the user choose full-data SFT, full-data DPO, or both; save one production
  weight per selected method and use the resulting models to score large mutation
  libraries for protein engineering decisions.

  '
metadata:
  category: supervised-finetuning-models
  legacy_category: workflow
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - model.supervised_finetuning
---

# mut_model Supervised Finetuning

Use this model-level skill for the checkout resolved as `MUT_MODEL_HOME` when a target protein
needs the full sequence:

1. Generate or collect a large pseudo-labeled mutation library.
2. Pretrain on pseudo labels from models such as SaProt, ESM-IF, GEMME, ProSST,
   S3F, or PoET.
3. Fine-tune with SFT, DPO, or both on all measured experimental rows without
   a train/test split.
4. Save one production weight per selected method.
5. Use the selected production models to score a large candidate library.

Do not treat this as the high-level supervised-finetuning workflow skill; it is
one implementation under the supervised fine-tuning model category.

## Mandatory Execution Contract

Execute these stages in order for every target:

1. Build and validate a target-specific pseudo-labeled mutation dataset.
2. Run pseudo-label pretraining and verify its target-specific checkpoint.
3. Let the user select SFT, DPO, or both.
4. Initialize every selected method independently from that same verified
   checkpoint and train on all real experimental rows without a split.
5. Save one full-data production weight per selected method.
6. Score candidates only with the corresponding saved production weights.

Never skip, reorder, or combine stages 1-5. Do not run `finetune_SFT.py` from a
random, generic, previously fine-tuned, or unrelated target checkpoint. A smoke
test does not count as pseudo-label pretraining. If pseudo labels cannot be
generated or pretraining fails, stop the workflow and report it as blocked;
do not fall back to direct supervised fine-tuning.

Use this as one of two interchangeable supervised-finetuning implementations
after the user has measured a small set of target-protein mutants. Choose it or
`venusfsfp-supervised-finetuning` based on the objective; do not run both by
default.

## Local Project

The model project and weights must resolve from `the resource configuration selected by ENZYME_CORE_RESOURCES (default: runtime/resources.json)` as
`MUT_MODEL_HOME`; do not guess an alternate path.

The repository bundles this project's source under
`enzyme_core/vendor/mut_model` (MIT); `MUT_MODEL_HOME` resolves there by default.
The bundled tree contains no datasets or weights. When the installed package
directory is read-only, copy the bundled project to a writable work directory and
point `MUT_MODEL_HOME` there before running training.

Run training commands from `$MUT_MODEL_HOME/model_run`, because the
project scripts use relative paths like `../data/...`.

Use the environment declared in `the resource configuration selected by ENZYME_CORE_RESOURCES (default: runtime/resources.json)`. A direct smoke test is:

```bash
cd "$MUT_MODEL_HOME/model_run"
"$CONDA_PREFIX/bin/python" test_smoke.py
```

## Input Files

`data/DMS_substitutions-test.csv` lists target IDs:

```csv
DMS_id
target_protein
```

For each target ID:

- `data/<DMS_id>-pretrain.csv`
  - Required: `mutated_sequence`
  - Required pseudo-label columns: choose from `saprot_fitness`,
    `prosst_fitness`, `esmif_fitness`, `gemme_fitness`, `s3f_fitness`,
    `poet_fitness`, or other numeric teacher outputs.
- `data/<DMS_id>.csv`
  - Required: `mutated_sequence`
  - Required: `DMS_score`
  - Required for DPO: an independent numeric teacher-reference column such as
    `saprot_fitness`; it must not duplicate `DMS_score`.
- candidate library for scoring:
  - Required: `mutated_sequence`
  - Optional: `mutation` for traceability.

Use `scripts/mut_model_tools.py` to construct these CSVs from mutation strings
or existing sequence tables.

## Prepare Data

Generate single saturation plus random multi-site candidates:

```bash
enzyme-core run mut-model-supervised-finetuning --work-dir ./runs/mut-model-supervised-finetuning --operation mut_model_tools -- generate-library \
  --sequence WT_SEQUENCE \
  --output work/target_library.csv \
  --max-sites 5 \
  --random-multisite 80000 \
  --seed 42
```

Convert teacher-scored pseudo labels to model input:

```bash
enzyme-core run mut-model-supervised-finetuning --work-dir ./runs/mut-model-supervised-finetuning --operation mut_model_tools -- prepare-pretrain \
  --sequence WT_SEQUENCE \
  --input teacher_scores.csv \
  --output "$MUT_MODEL_HOME/data/target_protein-pretrain.csv" \
  --y-columns saprot_fitness prosst_fitness esmif_fitness
```

Convert real measured data:

```bash
enzyme-core run mut-model-supervised-finetuning --work-dir ./runs/mut-model-supervised-finetuning --operation mut_model_tools -- prepare-finetune \
  --sequence WT_SEQUENCE \
  --input measured_mutants.csv \
  --score-column activity \
  --output "$MUT_MODEL_HOME/data/target_protein.csv"
```

Update the manifest:

```bash
printf 'DMS_id\ntarget_protein\n' > "$MUT_MODEL_HOME/data/DMS_substitutions-test.csv"
```

## Pseudo-Label Pretraining

Read `references/mut-model-notes.md` before editing project scripts.

In `model_run/pretrain.py`, set:

- `y_names` to the pseudo-label columns present in `<DMS_id>-pretrain.csv`.
- `train_mode = 'random'` unless `finetune_SFT.py` is also updated. The current
  finetune script loads `../pretrain_weight/pretrain-<DMS_id>-random.pkl`.
- `CUDA_VISIBLE_DEVICES` to the intended GPU or remove it.

Run:

```bash
cd "$MUT_MODEL_HOME/model_run"
"$CONDA_PREFIX/bin/python" pretrain.py
```

Expected output:

```text
$MUT_MODEL_HOME/pretrain_weight/pretrain-<DMS_id>-random.pkl
```

Before proceeding, verify and record all of the following:

- the pretraining command exited successfully;
- the checkpoint exists, is non-empty, and was created for the current
  `<DMS_id>` from the current `<DMS_id>-pretrain.csv`;
- the checkpoint mode in its filename matches the mode loaded by
  `finetune_SFT.py`;
- the pseudo-label columns are numeric, non-constant, and contain no unresolved
  missing values used as targets.

Treat this as a hard gate. If any item is unverified, do not start fine-tuning.

## Real-Data Supervised Finetuning

Enter this stage only after the pseudo-label pretraining gate above passes.
Fine-tuning must initialize from the verified target-specific pretraining
checkpoint; beginning from base model weights is prohibited in this skill.

Read `references/finetuning-options.md` before selecting the final method. Let
the user choose SFT, DPO, or both. If both are selected, run them independently
from the same verified pseudo-label pretraining weight and preserve two separate
production weights and prediction columns.

### SFT Production Option

The production default is one final fit over every valid row in
`data/<DMS_id>.csv`. Do not create train/test or cross-validation folds, and do
not reserve experimental rows for validation. Determine the epoch count before
this final fit from an approved fixed configuration or previously completed
diagnostic work; do not use a held-out subset during the production fit.
Complete and record the platform's required split-based model validation,
leakage check, uncertainty estimate, and out-of-distribution assessment before
this production stage; those diagnostics are separate from the final full-data
fit and their checkpoints must not be used for production scoring.

The current checkout of `model_run/finetune_SFT.py` only trains CV folds and
therefore does not satisfy this production requirement. Use the bundled
`scripts/finetune_full.py` production entrypoint. It:

- loads the verified `pretrain-<DMS_id>-random.pkl` state;
- builds one shuffled training loader from all valid experimental rows;
- performs no `create_cv_datasets` call and no test-set evaluation or
  test-metric early stopping;
- trains for the predetermined epoch count; and
- serializes the final NNX state to
  `finetune_weight/finetune-full-<DMS_id>-random.pkl`.

Confirm:

- `y_name = 'DMS_score'`
- The loaded pretrain file matches the pretraining mode.
- The number of rows consumed by the training loader equals the number of
  valid experimental rows after documented filtering.

Run the full-data entrypoint with an epoch count chosen before production
training:

```bash
enzyme-core run mut-model-supervised-finetuning --work-dir ./runs/mut-model-supervised-finetuning --operation finetune_full -- \
  --dms-id target_protein \
  --epochs 130 \
  --batch-size 64 \
  --seed 42
```

Expected outputs:

- `finetune_weight/finetune-full-<DMS_id>-random.pkl`
- `finetune_weight/finetune-full-<DMS_id>-random.pkl.json`, a run record
  containing the experimental-data checksum, row count,
  pretraining-weight checksum, epoch count, seed, final-weight checksum, and
  output path.

Verify that the production weight exists and is non-empty before scoring. Do
not use a fold-specific weight or select a production model by test-set
performance. If optional model evaluation is explicitly requested, perform it
as a separate diagnostic and still retrain the production model on all
experimental rows afterward.

### DPO and Both-Method Options

Use `scripts/finetune_dpo_full.py`, not the legacy few-shot driver, for DPO
production training. Follow `references/finetuning-options.md` for the required
teacher-reference column, commands, output names, independent initialization,
and separate SFT/DPO inference scores.

## Large-Scale Mutant Evaluation


The README mentions `evaluate.py`, but this checkout does not contain that
script. Use the bundled inference-only `score-library` command. It loads the
saved production state and performs no training or parameter updates:

For DPO-only or both-method runs, follow `references/finetuning-options.md` and
do not silently average SFT and DPO scores.

```bash
enzyme-core run mut-model-supervised-finetuning --work-dir ./runs/mut-model-supervised-finetuning --operation mut_model_tools -- score-library \
  --mut-model-dir "$MUT_MODEL_HOME" \
  --weight "$MUT_MODEL_HOME/finetune_weight/finetune-full-target_protein-random.pkl" \
  --input candidate_library.csv \
  --output ranked_candidates.csv \
  --batch-size 256
```

The score is in the model's normalized output scale. Use it for ranking within
the same target/run. Sort descending when higher experimental `DMS_score` means
better function; invert ranking only when the assay semantics say lower is
better.

After scoring, always write a quality report and candidate-set handoff before
passing supervised results into a custom chain QC stage:

```bash
enzyme-core run mut-model-supervised-finetuning --work-dir ./runs/mut-model-supervised-finetuning --operation mut_model_tools -- quality-report \
  --input ranked_candidates.csv \
  --output mut_model_quality.json \
  --mutation-column mutation \
  --score-column mut_model_score

enzyme-core run mut-model-supervised-finetuning --work-dir ./runs/mut-model-supervised-finetuning --operation mut_model_tools -- export-candidate-set \
  --input ranked_candidates.csv \
  --output mut_model_candidate_set.json \
  --mutation-column mutation \
  --score-column mut_model_score
```

Treat `mut_model_quality.json` status `blocked` as a failed supervised
handoff; do not continue to final QC with an empty, duplicate, or low-coverage
score table.

## Failure Handling

- If pseudo-label generation, validation, or pretraining fails, preserve the
  failure and stop before fine-tuning. Never bypass pretraining to obtain a
  result.
- If only fold-specific fine-tuning weights are available, do not start
  production library scoring. Complete the full-data fit and save its production
  weight first.
- If `pretrain.py` saves `pretrain-<DMS_id>-mixed.pkl` but finetune looks for
  `random.pkl`, rerun pretraining with `train_mode = 'random'` or update
  `finetune_SFT.py` consistently.
- If JAX raises a NumPy `copy=` compatibility error, copy the monkey patch from
  `model_run/test_smoke.py` into the script being executed.
- If DPO is selected but experimental mutants lack an independent numeric
  teacher-reference score, stop the DPO branch. Continue with SFT only if the
  user selected or accepts SFT.
- If import paths fail, run from `model_run` or pass `--mut-model-dir` to the
  bundled scorer.
- If all pseudo-label values in a column are identical, remove that teacher
  column; the dataloader cannot meaningfully normalize a constant target.
