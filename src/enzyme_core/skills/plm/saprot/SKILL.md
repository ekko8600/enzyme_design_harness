---
name: saprot
description: 'SaProt structure-aware zero-shot scoring for protein point mutants.
  Use this skill only to rank protein mutants by amino-acid plus Foldseek 3Di context-aware
  log probability ratio relative to the wild-type sequence. Do not use it for embeddings,
  masked amino-acid completion, generic sequence analysis, or representation comparison.

  '
metadata:
  category: plm
  tags: zeroshot, mutation-scoring, structure-aware, protein-mutants
  proteinbase_slug: saprot-structure-aware-plm
  proteinbase_url: https://proteinbase.com/design-methods/saprot-structure-aware-plm
  local_script: scripts/saprot_cli.py
  legacy_category: design-tools
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - science.general_operation
---

# SaProt Zero-Shot Mutant Scoring

Before running, read `the resource configuration selected by ENZYME_CORE_RESOURCES (default: runtime/resources.json)` and use
`model_weights.models.saprot` as the source of truth for local weights. In this
the resolved checkpoint is injected as `SAPROT_CHECKPOINT`;
do not download replacement checkpoints unless the user explicitly asks.

Use this skill only for zero-shot scoring of protein mutants with
structure-aware SaProt input. SaProt represents each residue as an amino-acid
token plus a Foldseek 3Di token, for example `Ev`, `Vp`, or `Q#`.

The mutant score is a summed log probability ratio:

```text
sum(log P(mutant amino acid | 3Di context) - log P(wild-type amino acid | 3Di context))
```

Higher scores mean the mutant is more favored relative to wild type under the
SaProt structure-aware masked language model.

## Prerequisites

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| Python | 3.10 | 3.10 |
| PyTorch | 1.13+ | 2.x |
| transformers | 4.28+ | current |
| Foldseek | for structure-to-3Di preparation | latest static binary |
| GPU VRAM | 8GB | 24GB for 650M |

Install runtime packages if needed:

```bash
pip install torch transformers biopython numpy pandas
```

## Input

Preferred input is a combined SaProt sequence with two characters per residue:

```text
M#EvVpQpL#VyQdYaKv
```

If starting from PDB/mmCIF, first prepare the combined sequence with Foldseek:

```bash
enzyme-core run saprot --work-dir ./runs/saprot --operation saprot_cli -- encode \
  --structure protein.cif \
  --foldseek-bin "$FOLDSEEK_BIN" \
  --chains A \
  --plddt-mask \
  --out-dir saprot_out
```

Use `encode` only as input preparation for mutation scoring.

## How to run

```bash
enzyme-core run saprot --work-dir ./runs/saprot --operation saprot_cli -- mutate \
  --sequence "M#EvVpQpL#VyQdYaKv" \
  --mutations V3A Q4M V3A:Q4M \
  --model "$SAPROT_CHECKPOINT/checkpoint/SaProt_650M_AF2" \
  --out-dir saprot_out
```

GPU is the default execution target for `mutate`. This CLI now defaults to
`--device cuda` and will fail fast if CUDA is unavailable. Use `--device cpu`
only when you intentionally accept a slower fallback.

Mutation strings are 1-indexed by protein residue, not by the two-character
SaProt token index. Combinatorial mutants use `:` between point mutations.

## Output

```text
saprot_out/mutation_scores.csv
```

Columns:

| Column | Meaning |
|--------|---------|
| `mutation` | Input mutation or mutation combination |
| `score` | Sum of mutant-vs-wild-type log probability ratios |

## Decision Rules

- Use `saprot` only when structure-aware AA+3Di context is available or can be
  prepared from a relevant structure.
- Use `esm` for sequence-only zero-shot mutant scoring.
- Use `esmif` for structure-conditioned scoring from backbone coordinates.
- SaProt is MSA-free. When fusing with GEMME/conservation, require the shared
  context MSA quality gate to pass before treating evolutionary disagreement as
  a strong reject signal.
- Do not use this skill for embeddings, masked-token completion, representation
  comparison, sequence design, or structure prediction.

## Verify

```bash
test -s saprot_out/mutation_scores.csv
```
