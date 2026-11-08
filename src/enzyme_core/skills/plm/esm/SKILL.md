---
name: esm
description: 'ESM-2 sequence-only zero-shot scoring for protein point mutants. Use
  this skill only to rank protein mutants by masked-marginal log probability ratio
  relative to the wild-type sequence. Do not use it for embeddings, generic sequence
  plausibility, structure prediction, sequence design, or masked amino acid completion.

  '
metadata:
  category: plm
  tags: zeroshot, mutation-scoring, protein-mutants
  proteinbase_slug: esm2-optimization
  proteinbase_url: https://proteinbase.com/design-methods/esm2-optimization
  local_script: scripts/local_esm2_zeroshot.py
  legacy_category: design-tools
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - sequence.mutation_scoring.language_model
---

# ESM-2 Zero-Shot Mutant Scoring

Before running, inspect `the resource configuration selected by ENZYME_CORE_RESOURCES (default: runtime/resources.json)` and use
`model_weights.models.esm` as the source of truth for local weights. Set
`TORCH_HOME` explicitly for the command; do not embed a machine-specific cache
path in the Skill.

Use this skill only for zero-shot scoring of protein mutants from an existing
wild-type amino-acid sequence. The score is:

```text
sum(log P(mutant amino acid at masked site) - log P(wild-type amino acid at masked site))
```

Higher scores mean the mutant residue is more favored than the wild-type
residue under ESM-2's sequence-only masked-marginal model.

## Prerequisites

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| Python | 3.8+ | 3.10 |
| PyTorch | 1.10+ | 2.x |
| fair-esm | current | current |
| GPU VRAM | 8GB | 24GB for 650M |

Install runtime packages if needed:

```bash
pip install torch fair-esm
```

## How to run

```bash
enzyme-core run esm --work-dir ./runs/esm --operation local_esm2_zeroshot -- \
  --sequence MKTAYIAKQRQISFVKSHFSRQDILDLWIYHTQGYFP \
  --mutations K5A R10H K5A:R10H \
  --model esm2_t33_650M_UR50D \
  --out-dir esm_zeroshot_out
```

GPU is the default execution target. This CLI now defaults to `--device cuda`
and will fail fast if CUDA is unavailable. Use `--device cpu` only when you are
explicitly choosing a slower fallback for a constrained environment.

Mutation strings are 1-indexed and must include the wild-type residue, such as
`K5A`. Combinatorial mutants use `:` between point mutations, such as
`K5A:R10H`.

## Output

```text
esm_zeroshot_out/mutation_scores.csv
```

Columns:

| Column | Meaning |
|--------|---------|
| `mutation` | Input mutation or mutation combination |
| `score` | Sum of mutant-vs-wild-type log probability ratios |
| `positions` | 1-indexed mutated positions |

## Model Choice

| Model | Best use |
|-------|----------|
| `esm2_t12_35M_UR50D` | Fast screening |
| `esm2_t33_650M_UR50D` | Default ranking |
| `esm2_t36_3B_UR50D` | Higher-cost reranking |

## Decision Rules

- Use `esm` when only the wild-type sequence and protein mutations are
  available.
- Use `esmif` when a backbone structure is available and structure-conditioned
  mutant scoring is desired.
- Use `saprot` when a SaProt AA+3Di sequence is available or can be prepared
  from a structure with Foldseek.
- ESM2 does not consume an MSA, but any ESM2-vs-GEMME conflict must be
  interpreted against the shared context MSA quality gate. Do not treat GEMME
  disagreement as strong evolutionary evidence when the context MSA is shallow,
  unaligned, or missing MMseqs2/ColabFold provenance.
- Do not use this skill for embeddings, contact prediction, structure
  prediction, sequence generation, or general sequence QC.

## Verify

```bash
test -s esm_zeroshot_out/mutation_scores.csv
```
