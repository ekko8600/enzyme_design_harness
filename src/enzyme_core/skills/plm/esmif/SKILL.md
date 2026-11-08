---
name: esmif
description: 'ESM-IF1 structure-conditioned zero-shot scoring for protein point mutants.
  Use this skill only to rank protein mutants by conditional log-likelihood difference
  relative to the wild-type sequence for a fixed protein backbone. Do not use it for
  sequence design, sequence sampling, embeddings, structure representation extraction,
  or structure prediction.

  '
metadata:
  category: plm
  tags: zeroshot, mutation-scoring, inverse-folding, protein-mutants
  local_script: scripts/local_esmif_zeroshot.py
  legacy_category: design-tools
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - science.general_operation
---

# ESM-IF1 Zero-Shot Mutant Scoring

Before running, read `the resource configuration selected by ENZYME_CORE_RESOURCES (default: runtime/resources.json)` and use the ESM/fair-esm local
runtime settings. ESM-IF1 weights resolve through the same Torch cache used by
ESM unless the user configures a different `TORCH_HOME`.

ESM-IF1 scores amino-acid sequences conditioned on fixed backbone coordinates.
Use this skill only for zero-shot comparison of protein mutants against the
wild-type sequence on the same PDB/mmCIF backbone.

The mutant score is:

```text
conditional_log_likelihood(mutant sequence | backbone)
- conditional_log_likelihood(wild-type sequence | backbone)
```

Higher scores mean the mutant sequence is more favored by ESM-IF1 for the given
backbone.

## Prerequisites

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| Python | 3.8+ | 3.10 |
| PyTorch | 1.10+ | 2.x |
| fair-esm | current | current |
| biotite | current | current |
| GPU VRAM | 4GB | 8GB+ |

Install runtime packages if needed:

```bash
pip install torch fair-esm biotite
```

## How to run

```bash
enzyme-core run esmif --work-dir ./runs/esmif --operation local_esmif_zeroshot -- \
  --structure protein.pdb \
  --chain A \
  --mutations K5A R10H K5A:R10H \
  --out-dir esmif_zeroshot_out
```

GPU is the default execution target. This CLI now defaults to `--device cuda`
and will fail fast if CUDA is unavailable. Use `--device cpu` only when you are
explicitly opting into a slower fallback.

If the sequence parsed from the structure is not the desired wild type, pass it
explicitly:

```bash
enzyme-core run esmif --work-dir ./runs/esmif --operation local_esmif_zeroshot -- \
  --structure protein.pdb \
  --chain A \
  --wildtype-sequence MKTAYIAKQRQISFVKSHFSRQDILDLWIYHTQGYFP \
  --mutations K5A R10H \
  --out-dir esmif_zeroshot_out
```

Mutation strings are 1-indexed and must include the wild-type residue.
Combinatorial mutants use `:` between point mutations.

## Output

```text
esmif_zeroshot_out/mutation_scores.csv
```

Columns:

| Column | Meaning |
|--------|---------|
| `mutation` | Input mutation or mutation combination |
| `wildtype_log_likelihood` | ESM-IF1 score for the wild-type sequence |
| `mutant_log_likelihood` | ESM-IF1 score for the mutant sequence |
| `delta_log_likelihood` | Mutant minus wild type |

## Decision Rules

- Use `esmif` only for mutant scoring when a relevant fixed backbone structure
  is available.
- Use `esm` when only sequence context is available.
- Use `saprot` when AA+3Di SaProt context is available.
- ESM-IF is structure-conditioned and MSA-free. When fusing with GEMME or
  conservation evidence, require the shared context MSA quality gate to pass
  before treating evolutionary disagreement as a strong reject signal.
- Do not use this skill to sample or design new sequences.

## Verify

```bash
test -s esmif_zeroshot_out/mutation_scores.csv
```
