# mut_model Production Fine-tuning Options

## Contents

- Selection
- Prepare experimental data
- SFT on all experimental rows
- DPO on all experimental rows
- Inference

Choose SFT, DPO, or both only after the target-specific pseudo-label pretraining
gate passes. Always train each selected production method on all valid
experimental rows without a train/test split.

## Selection

| Choice | Use when | Production output |
|---|---|---|
| SFT | Experimental values are reliable continuous targets and calibrated regression is useful | `finetune-full-<DMS_id>-random.pkl` |
| DPO | Final ranking/top-candidate separation matters and every experimental mutant has an independent teacher reference score | `finetune-dpo-full-<DMS_id>-random.pkl` |
| Both | Compute permits an independent-method comparison or model-disagreement analysis | Both weights and two separate score columns |

Do not choose DPO when an independent reference score is unavailable. Never use
`DMS_score` itself as the DPO reference. Suitable reference columns include
`saprot_fitness`, `esmif_fitness`, or another numeric teacher output scored on
the same measured mutants.

When using both methods, initialize SFT and DPO independently from the same
verified `pretrain-<DMS_id>-random.pkl`. Do not initialize DPO from the SFT
weight or SFT from the DPO weight. Do not average their predictions by default;
retain both scores and their disagreement.

## Prepare Experimental Data

Retain the selected DPO reference column when converting measured data:

```bash
enzyme-core run mut-model-supervised-finetuning --work-dir ./runs/mut-model-supervised-finetuning --operation mut_model_tools -- prepare-finetune \
  --sequence WT_SEQUENCE \
  --input measured_mutants.csv \
  --score-column activity \
  --reference-columns saprot_fitness \
  --output "$MUT_MODEL_HOME/data/target_protein.csv"
```

Omit `--reference-columns` for SFT-only training.

## SFT on All Experimental Rows

```bash
enzyme-core run mut-model-supervised-finetuning --work-dir ./runs/mut-model-supervised-finetuning --operation finetune_full -- \
  --dms-id target_protein \
  --epochs 130 \
  --batch-size 64 \
  --seed 42
```

The script must report `training_mode: all_experimental_rows_no_split` and save
the SFT production weight plus its JSON run record.

## DPO on All Experimental Rows

The legacy `$MUT_MODEL_HOME/model_run/finetune_DPO_fewshot.py` is a
ProteinGym few-shot evaluation script. It hardcodes ProteinGym/PDB paths,
creates many train/test subsets, reuses one model across subsets, and does not
save a final production state. Do not use it directly for production.

Use the bundled full-data wrapper, which reuses its
`PreferenceDataGenerator` and `SequenceDPOTrainer` loss while removing the
few-shot splits:

```bash
enzyme-core run mut-model-supervised-finetuning --work-dir ./runs/mut-model-supervised-finetuning --operation finetune_dpo_full -- \
  --dms-id target_protein \
  --reference-column saprot_fitness \
  --preference-percentile 90 \
  --epochs 150 \
  --steps-per-epoch 50 \
  --batch-size 32 \
  --n-pairs 10000 \
  --seed 42
```

The wrapper must verify that generated preference pairs cover every valid
experimental row. It saves the DPO production weight and a JSON run record with
the row coverage, input/pretrain/output checksums, training parameters, and
losses.

## Inference

Run inference independently for each selected production weight:

```bash
# SFT
enzyme-core run mut-model-supervised-finetuning --work-dir ./runs/mut-model-supervised-finetuning --operation mut_model_tools -- score-library \
  --weight "$MUT_MODEL_HOME/finetune_weight/finetune-full-target_protein-random.pkl" \
  --input candidate_library.csv \
  --output sft_ranked.csv \
  --score-column mut_model_sft_score

# DPO
enzyme-core run mut-model-supervised-finetuning --work-dir ./runs/mut-model-supervised-finetuning --operation mut_model_tools -- score-library \
  --weight "$MUT_MODEL_HOME/finetune_weight/finetune-dpo-full-target_protein-random.pkl" \
  --input candidate_library.csv \
  --output dpo_ranked.csv \
  --score-column mut_model_dpo_score
```

For a both-method run, join results by `mutation` or `mutated_sequence`, verify
one-to-one row identity, and preserve `mut_model_sft_score` and
`mut_model_dpo_score` as separate fields. Treat disagreement as model-validation
evidence rather than hiding it in an unexplained aggregate.
