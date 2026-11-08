---
name: uniprot
description: 'Access UniProt for protein sequence and annotation retrieval. Use this
  skill when: (1) Looking up protein sequences by accession, (2) Finding functional
  annotations, (3) Getting domain boundaries, (4) Finding homologs and variants, (5)
  Cross-referencing to PDB structures.

  For structure retrieval, use pdb. For sequence design, use proteinmpnn.

  '
metadata:
  category: data
  tags: database, sequence, annotation
  legacy_category: utilities
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - data.scientific_database.retrieval
---

# UniProt Database Access

**Quick alias**: the canonical implementation is `uniprot-database`. Use its
`uniprot_tools.py` wrapper for every UniProt access — it handles pagination,
streaming, ID mapping, SPARQL, rate limits, and the license notification. Do
**not** hand-roll `curl` or `requests` calls against UniProt REST APIs; that
bypasses the documented wrapper and is a discipline violation under AGENTS.md.

## Fetching a Sequence or Entry

```bash
# Get a single known accession (metadata + sequence)
enzyme-core run uniprot-database --work-dir ./runs/uniprot-database --operation uniprot_tools -- get P00533

# Historical / deleted accession (UniSave)
enzyme-core run uniprot-database --work-dir ./runs/uniprot-database --operation uniprot_tools -- get P00533 --dataset unisave
```

## Searching

```bash
# Explore with a small limit before committing to bulk
enzyme-core run uniprot-database --work-dir ./runs/uniprot-database --operation uniprot_tools -- search "gene:EGFR AND reviewed:true" --limit 5

# Count first, always
enzyme-core run uniprot-database --work-dir ./runs/uniprot-database --operation uniprot_tools -- count "taxonomy_id:9606 AND reviewed:true"

# Bulk retrieval (streams the full result set)
enzyme-core run uniprot-database --work-dir ./runs/uniprot-database --operation uniprot_tools -- stream \
  "taxonomy_id:9606 AND reviewed:true" --format tsv --fields accession,gene_names > human_reviewed.tsv
```

## ID Mapping

```bash
enzyme-core run uniprot-database --work-dir ./runs/uniprot-database --operation uniprot_tools -- map "P00533" \
  --from_db UniProtKB_AC-ID --to_db Gene_Name
```

## SPARQL (exact sequence lookup, complex discovery)

```bash
enzyme-core run uniprot-database --work-dir ./runs/uniprot-database --operation uniprot_tools -- sparql \
  'PREFIX up: <http://purl.uniprot.org/core/> SELECT ?protein WHERE { ?protein a up:Protein ; up:reviewed true . } LIMIT 5'
```

## Cross-References

Use `get` output and the `map` tool to find PDB structures, taxonomy,
orthologs, and external database links for an accession. For PDB-specific
structure retrieval, switch to `pdb-database`.

## Common Use Cases

1. Find protein by name → `search "insulin receptor" organism:9606`.
2. Get accession from results.
3. Get domains / variants from the `get` JSON features.
4. Cross-reference to PDB via `map` or `get` xrefs.

## Troubleshooting

**Entry not found**: Check accession format (e.g., P00533); try UniParc for
non-model organisms via `get --dataset uniparc`.
**Large downloads**: Use `stream` (bulk) or `count` first; `search` paginates
beyond 500 entries but is for exploration.
**Wrapper missing/error**: Read `uniprot-database/SKILL.md`; do not fall back
to raw HTTP without recording the wrapper failure first.

---

**Next**: Use sequence with `esm` for zero-shot scoring or `boltz` for
structure prediction.
