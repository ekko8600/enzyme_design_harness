---
name: gemme
description: 'GEMME evolutionary zero-shot scoring for protein point mutants from
  an MSA. Use this skill only to rank missense mutants by GEMME fitness/evolutionary
  scores when a homologous multiple sequence alignment is available in A2M-like FASTA
  format. Do not use it without an MSA, for embeddings, structure prediction, sequence
  design, or general sequence plausibility.

  '
metadata:
  category: evolution
  tags: zeroshot, mutation-scoring, evolutionary, msa, protein-mutants
  local_script: scripts/gemme_cli.py
  legacy_category: design-tools
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - sequence.mutation_scoring.evolutionary
---

# GEMME Zero-Shot Mutant Scoring

Before running, read `the resource configuration selected by ENZYME_CORE_RESOURCES (default: runtime/resources.json)` and use
`model_weights.models.gemme` as the source of truth for the local GEMME setup.
Set `GEMME_HOME` from that configuration before execution. GEMME runs through
the configured Docker image `elodielaine/gemme:gemme`.

Use this skill only for zero-shot scoring of protein point mutants when a
homologous MSA is available. GEMME assumes the first sequence in the alignment
is the query/wild-type sequence.

Higher GEMME scores are more favorable. Scores are evolutionary evidence from
the supplied MSA and should be interpreted with MSA depth, coverage, redundancy,
and taxonomy bias in mind.

## Prerequisites

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| Docker | installed | image already loaded/pullable |
| Python | 3.8+ | 3.10+ |
| MSA | A2M-like FASTA | query first, full mutation coverage |

If the image tar exists but the image is not loaded:

```bash
docker load -i "$GEMME_DOCKER_IMAGE_TAR"
```

## Input

Required inputs:

- Wild-type sequence, either from `--sequence` or inferred from the first MSA
  record.
- Mutations such as `K5A` or combinations such as `K5A:R10H`.
- MSA in A2M-like FASTA format with the query sequence first.

If the available alignment comes from `protein-sequence-msa` or another search
tool and is not A2M-like FASTA, normalize it first:

```bash
enzyme-core run protein-sequence-msa --work-dir ./runs/protein-sequence-msa --operation msa_to_a2m -- \
  input_alignment.fa \
  --query-sequence MKTAYIAKQRQISFVKSHFSRQDILDLWIYHTQGYFP \
  --msa-start 1 \
  --drop-lowercase-inserts \
  --output gemme_input.a2m
```

For high-quality GEMME input, prefer the same MMseqs2 search settings used by
Boltz/ColabFold-style MSA generation: UniRef plus MGnify/environmental hits,
query first, and up to roughly 1000 diverse hits for screening. The
`protein-sequence-similarity-search` script can export `combined_cleaned.a3m`;
use that file as the GEMME source when available. Do not pass raw A3M lowercase
insert states into GEMME column mapping.

## How to run

```bash
enzyme-core run gemme --work-dir ./runs/gemme --operation gemme_cli -- score \
  --msa gemme_input.a2m \
  --sequence MKTAYIAKQRQISFVKSHFSRQDILDLWIYHTQGYFP \
  --mutations K5A R10H K5A:R10H \
  --msa-start 1 \
  --out-dir gemme_zeroshot_out
```

Mutation strings are 1-indexed and must include the wild-type residue. If the
MSA covers only a subsequence, set `--msa-start` to the first wild-type residue
number covered by the MSA; all scored mutations must fall inside that range.

Use `--mutation-csv candidates.csv` when mutations are already in a CSV column
named `mutation` or `mutant`.

## Output

```text
gemme_zeroshot_out/mutation_scores.csv
```

Columns:

| Column | Meaning |
|--------|---------|
| `mutation` | Input mutation or mutation combination |
| `score` | GEMME score, higher is more favorable |
| `GEMME_score` | Same score under the original GEMME naming |
| `positions` | 1-indexed mutated positions |

The script also writes `gemme_zeroshot_out/gemme_stdout.log` and
`gemme_zeroshot_out/gemme_stderr.log` for debugging failed Docker runs.

## Decision Rules

- Use `gemme` when an MSA exists and evolutionary conservation/context should
  contribute to mutant ranking.
- Prefer deep MMseqs2 A3M-derived alignments over shallow hand-collected FASTA
  sets when the shallow set has few close homologs.
- Use `esm` when only the wild-type sequence is available.
- Use `esmif` when a fixed backbone structure is available.
- Use `saprot` when structure-aware AA+3Di context is available.
- Do not compare GEMME scores across unrelated MSAs as absolute fitness values.

## Verify

```bash
test -s gemme_zeroshot_out/mutation_scores.csv
```
