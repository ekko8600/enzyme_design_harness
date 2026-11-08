---
name: venusfsfp-supervised-finetuning
description: 'Supervised protein fitness fine-tuning via VenusFSFP. Always use large
  pseudo-labeled mutation libraries (single-site saturation + random multi-site) scored
  by a zero-shot model (SaProt or GEMME) for meta pre-training, verify the target-specific
  pretraining checkpoint, and only then perform meta-transfer on all real experimental
  labels without a train/test split, save the resulting production checkpoint, and
  use it for large-library scoring. Direct few-shot fine-tuning is not allowed. Use
  when Codex needs to convert user-supplied protein mutant assay data into VenusFSFP
  format, meta-transfer an ESM-2 LoRA model, and rank new mutation libraries.

  '
metadata:
  category: supervised-finetuning-models
  legacy_category: workflow
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - model.supervised_finetuning
---

# VenusFSFP Supervised Finetuning

Use this workflow for protein-specific supervised fine-tuning when the user
provides a wild-type sequence, measured mutant fitness/activity values, and
optionally a new candidate mutation table to rank.

Do not use this skill for zero-shot-only screening; use individual PLM skills for that.

Within the project loop, use this as one of two interchangeable
`supervised_finetuning` implementations for second-round mutant ranking after
the user has measured a small set of target-protein mutants. The loop should
recommend choosing either this skill or `mut-model-supervised-finetuning`, not
both by default.

## Mandatory Execution Contract

Execute these stages in order for every target:

1. Generate a target-specific mutation library.
2. Produce and validate numeric pseudo labels with at least one teacher model.
3. Run `--mode meta` pretraining on the pseudo-labeled library and verify the
   target-specific checkpoint.
4. Run one `--mode meta-transfer --cross_validation 0` fit on all real
   experimental labels, initialized from that verified checkpoint.
5. Verify and preserve the resulting production checkpoint.
6. Predict and rank new candidates only with that checkpoint.

Never skip, reorder, or combine stages 1-5. Do not use `--mode finetune` as a
shortcut and do not initialize meta-transfer from a generic, unrelated, or
unverified checkpoint. If no teacher can produce usable pseudo labels, or if
pretraining fails, stop and report the workflow as blocked; do not fall back to
direct few-shot training.

Prefer the teacher with the strongest validated correlation to the experimental
scores. A correlation at or below 0.5 is a model-applicability warning, not
permission to bypass pretraining: record the limitation, try another available
teacher, and block if none is scientifically usable.

## Dependencies

- Local VenusFSFP project resolved from `the resource configuration selected by ENZYME_CORE_RESOURCES (default: runtime/resources.json)` and injected as
  `VENUSFSFP_HOME`; do not guess or hard-code an alternate checkout.
- Conda environment declared in `the resource configuration selected by ENZYME_CORE_RESOURCES (default: runtime/resources.json)` and verified directly before execution.
- Zero-shot scoring (one or more of):
  - `saprot` / `esmif`: PLM zero-shot scoring, MSA-free.
  - `gemme`: evolutionary pseudo-labels. Requires `protein-sequence-msa` for
    MSA normalization and an A2M-like MSA with the query sequence first.
- Mutation library generation:
  - `mut-model-supervised-finetuning/scripts/mut_model_tools.py generate-library`
    for building single-site saturation and random multi-site libraries.

## Required Inputs

- Wild-type protein sequence using standard amino acids.
- Experimental mutant table with a mutation column (`mutant` or `mutation`) and
  a numeric response column. Mutations must be 1-indexed substitutions such as
  `A23V` or multi-site groups such as `A23V:G57D`.
- Candidate mutation table for prediction, when ranking new variants.
- MSA covering the mutated positions if GEMME augmentation is requested.

VenusFSFP raw CSV columns:

- `mutant`
- `mutated_sequence`
- `DMS_score`
- `DMS_score_bin`
- One or more augmentation columns for `--augment`: `GEMME`, `saprot_fitness`,
  `esmif_fitness`, etc. Column names must match the `--augment` value.

Use `scripts/venusfsfp_tools.py` to create base columns. Add augmentation
columns with the `gemme` skill, zero-shot PLM scoring, or direct CSV merge.

---

## Required Workflow: Pseudo-Label Pretraining Then Meta-Transfer

This workflow generates a large synthetic mutation library, scores it with a
zero-shot model as pseudo-labels, pre-trains a meta-model, then fine-tunes
with real experimental labels.

### A.1 Setup

Copy VenusFSFP into a per-run directory:

```bash
cp -r "$VENUSFSFP_HOME" VenusFSFP_run
```

### A.2 Generate Large Mutation Library

Generate single-site saturation mutations plus random multi-site mutations
(2-3 sites). For a 194-residue protein this yields ~3,686 single-site +
~30,000 multi-site mutants.

```bash
python supervised-finetuning-models/mut-model-supervised-finetuning/scripts/mut_model_tools.py generate-library \
  --sequence WT_SEQUENCE \
  --max-sites 3 \
  --random-multisite 30000 \
  --seed 42 \
  --output large_library.csv
```

### A.3 Score Library with Zero-Shot Model

Score the full library with the chosen zero-shot model. Use SaProt when its
Spearman correlation with experimental scores exceeds 0.5; fall back to GEMME
(evolutionary) or other PLMs.

```bash
# Option 1: SaProt zero-shot (recommended when r > 0.5)
enzyme-core run saprot --work-dir ./runs/saprot --operation saprot_cli -- score \
  --sequence WT_SEQUENCE \
  --structure target.pdb \
  --input large_library.csv \
  --mutation-column mutation \
  --output large_library_scored.csv

# Option 2: GEMME evolutionary scoring
enzyme-core run gemme --work-dir ./runs/gemme --operation gemme_cli -- score \
  --msa target.a2m \
  --sequence WT_SEQUENCE \
  --mutation-csv large_library.csv \
  --mutation-column mutation \
  --out-dir gemme_out
```

### A.4 Prepare Pseudo-Labeled Pre-training Data

Build a VenusFSFP CSV using the zero-shot scores as `DMS_score`, plus the
same column as `--augment` source. Include GEMME columns when available.

```bash
enzyme-core run venusfsfp-supervised-finetuning --work-dir ./runs/venusfsfp-supervised-finetuning --operation venusfsfp_tools -- prepare-training \
  --sequence WT_SEQUENCE \
  --protein TARGET \
  --assay pretrain \
  --input large_library_scored.csv \
  --score-column saprot_fitness \
  --mutation-column mutation \
  --output VenusFSFP_run/data/substitutions/TARGET_pretrain_assay.csv
```

Then add augmentation columns (GEMME, ESM-IF, etc.) to the CSV:

```bash
enzyme-core run venusfsfp-supervised-finetuning --work-dir ./runs/venusfsfp-supervised-finetuning --operation venusfsfp_tools -- merge-gemme \
  --venus-csv VenusFSFP_run/data/substitutions/TARGET_pretrain_assay.csv \
  --gemme-csv gemme_out/mutation_scores.csv \
  --output VenusFSFP_run/data/substitutions/TARGET_pretrain_assay.csv
```

### A.5 Prepare Real-Labeled Fine-tuning Data

Convert experimental data into VenusFSFP format (separate dataset):

```bash
enzyme-core run venusfsfp-supervised-finetuning --work-dir ./runs/venusfsfp-supervised-finetuning --operation venusfsfp_tools -- prepare-training \
  --sequence WT_SEQUENCE \
  --protein TARGET \
  --assay finetune \
  --input measured_mutants.csv \
  --score-column activity \
  --output VenusFSFP_run/data/substitutions/TARGET_finetune_assay.csv
```

Merge the same augmentation columns into the real-labeled CSV too:

```bash
enzyme-core run venusfsfp-supervised-finetuning --work-dir ./runs/venusfsfp-supervised-finetuning --operation venusfsfp_tools -- merge-gemme \
  --venus-csv VenusFSFP_run/data/substitutions/TARGET_finetune_assay.csv \
  --gemme-csv gemme_out/mutation_scores.csv \
  --output VenusFSFP_run/data/substitutions/TARGET_finetune_assay.csv
```

### A.6 Preprocess and Retrieve

```bash
cd VenusFSFP_run
python preprocess.py
python retrieve.py -m vectorize -md esm2 -b 8
python retrieve.py -m retrieve -md esm2 -b 16 -k 1 -mt cosine -cpu
```

### A.7 Meta Pre-training on Pseudo-Labels

Train on the large pseudo-labeled dataset. Use full data (no CV) since
pseudo-labels are abundant. Adjust `--train_size` to the pseudo-labeled row
count.

```bash
python main.py --mode meta --model esm2 --protein TARGET_pretrain \
  --train_size PSEUDO_LABELED_COUNT --train_batch 1 --lora_r 16 \
  --learning_rate 1e-4 --epochs 50 --patience 15 --list_size 10 \
  --max_iter 5 --retr_metric cosine --augment saprot_fitness \
  --meta_tasks 1 --meta_train_batch 16 --meta_eval_batch 128 \
  --adapt_lr 5e-3 --adapt_steps 5 --cross_validation 0 --n_sites 0
```

Before proceeding, verify and record all of the following:

- the meta-pretraining command exited successfully;
- the checkpoint directory exists, is non-empty, and belongs to the current
  target, pseudo-labeled dataset, model, and LoRA configuration;
- the pseudo-label dataset row count and `--train_size` agree;
- the selected `--augment` column exists, is numeric, non-constant, and has no
  unresolved missing values used for training;
- the meta-transfer checkpoint lookup resolves to this exact pretraining run.

Treat this as a hard gate. If any item is unverified, do not start
meta-transfer.

### A.8 Meta-transfer with Real Experimental Labels

Enter this stage only after the pseudo-label pretraining gate passes. Fine-tune
the verified target-specific checkpoint on every valid experimental row. This
full-data fit is the default and required production path. Do not create a
train/test split or run cross-validation in this stage. Set `--train_size` to
the complete valid experimental row count and `--cross_validation 0`.

Complete and record the platform's required split-based model validation,
leakage check, uncertainty estimate, and out-of-distribution assessment before
this production stage; keep those diagnostics separate from the final full-data
checkpoint.

Run:

```bash
python main.py --mode meta-transfer --model esm2 --protein TARGET_finetune \
  --train_size REAL_LABELED_COUNT --train_batch 16 --lora_r 16 \
  --learning_rate 1e-4 --epochs 50 --patience 15 --list_size 10 \
  --max_iter 5 --retr_metric cosine --augment saprot_fitness \
  --meta_tasks 1 --meta_train_batch 16 --meta_eval_batch 128 \
  --adapt_lr 5e-3 --adapt_steps 5 --cross_validation 0 --n_sites 0
```

With `--cross_validation 0`, VenusFSFP trains without a validation loader and
`TrainerBase` saves the final LoRA/model state at the end of training. After the
command completes, locate the corresponding directory under
`checkpoints/meta-transfer/`, then verify and record:

- the command exited successfully;
- all valid experimental rows were consumed and the row count equals
  `REAL_LABELED_COUNT`;
- the checkpoint directory contains the saved model/adapter files and
  `logs.pkl`;
- the checkpoint belongs to the current target and verified pseudo-label
  pretraining run; and
- the experimental-data, pretraining-checkpoint, and final-checkpoint
  checksums are recorded in the run state.

Treat this saved directory as the production checkpoint. If optional split-based
evaluation is explicitly requested, run it separately and then repeat this
full-data `--cross_validation 0` fit; never use a split-specific checkpoint as
the production scoring model.

When `--n_sites 0` is used and the checkpoint path does not include `_ms`,
create a symlink so meta-transfer can locate the checkpoint:

```bash
CHECKPOINT_DIR=$(ls -td checkpoints/meta/esm2/TARGET_pretrain_assay/r16_* | head -1)
ln -s $(basename "$CHECKPOINT_DIR") "${CHECKPOINT_DIR}_ms"
```

---

## Prediction on New Candidates

Predict only after both the pseudo-label pretraining gate and real-label
full-data meta-transfer have completed successfully and the production
checkpoint has been saved. Do not use a pretraining-only, base-model,
direct-finetune, or split-specific checkpoint for this stage.

Do not use `main.py --test --epochs 0`: that command loads the base model rather
than the saved production checkpoint. Use the bundled inference-only scorer,
which explicitly loads the ESM-2 LoRA adapter from `--checkpoint` and never
updates model parameters:

```bash
enzyme-core run venusfsfp-supervised-finetuning --work-dir ./runs/venusfsfp-supervised-finetuning --operation score_checkpoint -- \
  --venusfsfp-dir "$VENUSFSFP_HOME" \
  --checkpoint VenusFSFP_run/checkpoints/meta-transfer/esm2/PRODUCTION_CHECKPOINT \
  --sequence WT_SEQUENCE \
  --input new_library.csv \
  --mutation-column mutation \
  --output venus_predictions.csv
```

The input must contain 1-indexed substitutions such as `A23V` or
`A23V:G57D`. The scorer validates every wild-type residue, writes
`VenusFSFP_score`, and emits `venus_predictions.csv.json` with checkpoint and
input checksums. The score is the same sum of mutant-minus-wild-type ESM-2 log
probabilities used by the VenusFSFP ESM-2 ranking path. Sort descending when
"higher is better"; reverse only when assay semantics require lower values.

After prediction, write a quality report and candidate-set handoff:

```bash
enzyme-core run venusfsfp-supervised-finetuning --work-dir ./runs/venusfsfp-supervised-finetuning --operation venusfsfp_tools -- quality-report \
  --input venus_predictions.csv \
  --output venusfsfp_quality.json \
  --mutation-column mutant \
  --score-column VenusFSFP_score

enzyme-core run venusfsfp-supervised-finetuning --work-dir ./runs/venusfsfp-supervised-finetuning --operation venusfsfp_tools -- export-candidate-set \
  --input venus_predictions.csv \
  --output venusfsfp_candidate_set.json \
  --mutation-column mutant \
  --score-column VenusFSFP_score
```

Treat `venusfsfp_quality.json` status `blocked` as a failed supervised handoff;
do not continue to final QC with an empty, duplicate, or low-coverage prediction
table.

---

## Augmentation Model Selection

`--augment` accepts any column name present in the raw CSV. Choose the
augmentation model with the highest Spearman correlation to experimental scores.
Common options:

| Augment Value | Source | Requires |
|---------------|--------|----------|
| `saprot_fitness` | SaProt zero-shot | Structure PDB for foldseek |
| `GEMME` | GEMME evolutionary | MSA (A2M format) |
| `esmif_fitness` | ESM-IF inverse folding | Structure PDB |
| Any CSV column | User-provided | Present in raw CSV |

The pipeline reads the `--augment` column from the raw CSV and substitutes its
values as pseudo-labels for meta-task augmentation. The column name must match
exactly.

## Parameter Rules

- **Large-library pre-training**: `--train_size` = full library size, `--epochs 50`,
  `--cross_validation 0` (no validation, pseudo-labels are abundant).
- **Meta-transfer fine-tuning**: `--train_size` = real experimental data count,
  `--epochs 30-50`, `--cross_validation 0`; consume all valid experimental rows
  and save the resulting production checkpoint.
- Set `meta_tasks` to available auxiliary proteins, not blindly to 3.
- Use ESM-2 first. Use SaProt only when `data/struc_sq_72.csv` has a matching
  structure sequence for the protein.
- For very small labeled sets, reduce `list_size` to 2-5. Use `list_size 1` for
  regression-style training only.
- Use `--n_sites 0` to include all mutation site counts (single, double, triple).

## Failure Handling

- If pseudo-label generation, validation, or meta-pretraining fails, preserve
  the failure and stop before meta-transfer. Never bypass pretraining to obtain
  a result.
- If full-data meta-transfer does not save a valid production checkpoint, stop
  before large-library scoring. Do not substitute a CV or held-out-split
  checkpoint.
- If the project import fails in Python 3.12 because of `learn2learn`, verify
  that VenusFSFP has the lazy MAML import described in
  `references/venusfsfp-notes.md`.
- If `torch.load` fails with `weights_only`, patch VenusFSFP loads to pass
  `weights_only=False`.
- If Hugging Face is unreachable, use the local ESM-2 snapshot configured in
  `fsfp/config.json`; do not start by downloading new 650M weights.
- If meta-transfer cannot find the meta checkpoint due to `_ms` path suffix
  mismatch (triggered by `--n_sites 0`), create a symlink:
  `ln -s <actual_dir> <actual_dir>_ms` in the checkpoint directory.
- If no MSA is available, use an MSA-free teacher such as SaProt or ESM-IF when
  its required structure input is available. If no teacher yields usable
  pseudo labels, report the workflow as blocked.
- If `--augment` column is missing from the CSV, verify the column name matches
  exactly and the column exists in `data/substitutions/<protein>.csv`.

Read `references/venusfsfp-notes.md` before changing VenusFSFP code or tuning
parameters beyond the defaults above.
