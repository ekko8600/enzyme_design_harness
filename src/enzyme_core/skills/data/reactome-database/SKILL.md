---
name: reactome-database
description: 'Query the Reactome database (Analysis and Content Services). Use when
  the user asks about pathway analysis, gene list enrichment, retrieving results by
  token, finding unmapped or not-found identifiers, mapping identifiers, reaction
  participants (inputs, outputs), pathway hierarchy (including top-level pathways),
  diagram export, cross-reference mapping, or searching the knowledgebase.

  '
metadata:
  category: data
  legacy_category: databases
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - data.scientific_database.retrieval
---

# Reactome Analysis & Content Service

## Prerequisites

1.  **Python**: Run scripts directly with `python`; do not require `uv` unless the script explicitly fails without it.
2.  **User Notification**: If LICENSE_NOTIFICATION.txt does not already exist in
    this skill directory then (1) prominently notify the user to check the terms
    at https://reactome.org/license and https://reactome.org/cite, then (2)
    create the file recording the notification text and timestamp.

## Overview

Reactome is a free, open-source, curated pathway database. This skill wraps both
the **Analysis Service** (`https://reactome.org/AnalysisService/`) and the
**Content Service** (`https://reactome.org/ContentService/`) providing pathway
enrichment analysis, identifier mapping, reaction details, pathway hierarchy
navigation, diagram export, cross-reference mapping, and search.

## When to Use This Skill

-   Performing pathway enrichment (overrepresentation) analysis on gene/protein
    lists
-   Retrieving analysis results using a token from previous enrichment
-   Identifying which genes or proteins were not found in a pathway analysis
-   Analyzing gene expression data against pathway annotations
-   Mapping identifiers to Reactome entities across species
-   Retrieving reaction participants (inputs, outputs, catalysts, regulators)
-   Navigating pathway hierarchy and listing top-level pathways
-   Finding which complexes or sets contain a protein
-   Exporting pathway/reaction diagrams (PNG/SVG) with gene highlighting
-   Cross-referencing identifiers across databases (UniProt, Ensembl, etc.)
-   Searching the Reactome knowledgebase
-   Downloading analysis reports (PDF, CSV, JSON)
-   Comparing pathways across species

## Common Species IDs

Reference list for common research organisms:

-   Homo sapiens
    -   ID: 9606
-   Mus musculus (Mouse)
    -   ID: 48892
-   Rattus norvegicus (Rat)
    -   ID: 48895

## Common Pathway IDs

Reference list for commonly used Reactome pathway stable IDs:

-   Cell Cycle
    -   Stable ID: R-HSA-1640170
    -   Notes: Top-level pathway (broad)
-   Cell Cycle, Mitotic
    -   Stable ID: R-HSA-69278
    -   Notes: Specific sub-pathway — use this for diagrams and drill-downs
-   Immune System
    -   Stable ID: R-HSA-168256
    -   Notes: Top-level pathway
-   Signal Transduction
    -   Stable ID: R-HSA-162582
    -   Notes: Top-level pathway
-   Gene Expression
    -   Stable ID: R-HSA-74160
    -   Notes: Top-level pathway
-   Programmed Cell Death
    -   Stable ID: R-HSA-5357801
    -   Notes: Top-level pathway

> **Important**: When the user asks for a "Cell Cycle" diagram or analysis,
> prefer the specific **Cell Cycle, Mitotic** pathway (`R-HSA-69278`) unless the
> user explicitly requests the top-level overview. The examples throughout this
> document use `R-HSA-69278`.

## Core Rules

1.  **Always use `--output`**: Every subcommand requires `--output <file>` to
    write results to a file. Never rely on stdout for large results.
2.  **Default species is Homo sapiens**: Use `--species` to override.
3.  **Tokens expire after 7 days**: Store tokens from analysis results to
    retrieve them later without re-submitting data.
4.  **Use `--fdr` and `--pvalue` to filter**: Enrichment results can be
    overwhelming. Filter with `--fdr 0.05` or `--pvalue 0.01` to focus on
    statistically significant pathways.
5.  **Identifier formats**: Reactome auto-detects identifiers including gene
    symbols (TP53), UniProt (P04637), Ensembl (ENSG00000141510), ChEBI, OMIM,
    EntrezGene, and many more.
6.  **Handle large outputs**: For commands that return large data (like
    `species-comparison`), use the `--summary` flag to truncate lists and avoid
    exceeding workspace file size limits (1MB).
7.  **Notification**: If this skill is used, ensure this is mentioned in the
    output.

## Tool Execution

The CLI tool is at `scripts/reactome_analysis.py`. Run with `python`:

```bash
python scripts/reactome_analysis.py <command> [options] --output /tmp/out.json
```

**To list all available subcommands and flags**, run:

```bash
python scripts/reactome_analysis.py --help
```

Use `--help` to verify available subcommands or flags before executing an
unfamiliar command.

## Feature Domains

### 1. Database Info

```bash
python scripts/reactome_analysis.py db-version --output /tmp/version.json
python scripts/reactome_analysis.py db-name --output /tmp/name.json
```

### 2. Single Identifier Analysis

```bash
python scripts/reactome_analysis.py identifier --id TP53 --output /tmp/tp53.json
python scripts/reactome_analysis.py identifier-projection --id TP53 --output /tmp/tp53_proj.json
```

### 3. Batch Analysis (Enrichment)

Submit a list of identifiers for overrepresentation or expression analysis:

```bash
python scripts/reactome_analysis.py analyze --data "TP53,BRCA1,EGFR" --output /tmp/enrich.json
python scripts/reactome_analysis.py analyze --file genes.txt --output /tmp/enrich.json
python scripts/reactome_analysis.py analyze-projection --data "TP53,BRCA1" --output /tmp/proj.json
python scripts/reactome_analysis.py analyze --data "TP53,BRCA1" --fdr 0.05 --output /tmp/sig.json
```

Common options: `--page-size` (alias `--limit`), `--page` (alias `--offset`),
`--sort-by`, `--order`, `--resource`, `--species`, `--fdr`, `--pvalue`.

### 4. Token-Based Result Retrieval

```bash
python scripts/reactome_analysis.py token-result --token TOKEN --output /tmp/result.json
python scripts/reactome_analysis.py token-not-found --token TOKEN --output /tmp/notfound.json
python scripts/reactome_analysis.py token-resources --token TOKEN --output /tmp/resources.json
python scripts/reactome_analysis.py token-found-entities --token TOKEN --pathway R-HSA-69278 --output /tmp/found.json
python scripts/reactome_analysis.py token-filter-species --token TOKEN --species-filter 9606 --output /tmp/filtered.json
python scripts/reactome_analysis.py token-reactions-pathway --token TOKEN --pathway R-HSA-69278 --output /tmp/rxns.json
```

### 5. Download Results

```bash
python scripts/reactome_analysis.py download-result --token TOKEN --output /tmp/full.json
python scripts/reactome_analysis.py download-pathways --token TOKEN --output /tmp/pathways.csv
python scripts/reactome_analysis.py download-found --token TOKEN --output /tmp/found.csv
python scripts/reactome_analysis.py download-not-found --token TOKEN --output /tmp/notfound.csv
```

### 6. Identifier Mapping

```bash
python scripts/reactome_analysis.py mapping --data "TP53,BRCA1" --output /tmp/mapped.json
python scripts/reactome_analysis.py mapping-projection --data "TP53" --output /tmp/mapped_proj.json
```

### 7. Reaction Participants & Mechanism of Action

Retrieve the molecular participants of a reaction (inputs, outputs, catalysts):

```bash
python scripts/reactome_analysis.py participants --id R-HSA-6804194 --output /tmp/participants.json
python scripts/reactome_analysis.py participating-entities --id R-HSA-6804194 --output /tmp/entities.json
```

### 8. Complex & Set Membership

Find which complexes or sets contain a given entity:

```bash
python scripts/reactome_analysis.py component-of --id R-HSA-69488 --output /tmp/complexes.json
```

### 9. Pathway Hierarchy Navigation

Move up (ancestors) or down (contained events) the pathway hierarchy:

```bash
python scripts/reactome_analysis.py event-ancestors --id R-HSA-69278 --output /tmp/ancestors.json
python scripts/reactome_analysis.py contained-events --id R-HSA-69278 --output /tmp/steps.json
python scripts/reactome_analysis.py top-pathways --output /tmp/top.json
python scripts/reactome_analysis.py low-pathways --id R-HSA-69488 --output /tmp/low.json
```

### 10. Diagram Export

Export pathway or reaction diagrams as PNG/SVG, with optional gene highlighting:

```bash
python scripts/reactome_analysis.py diagram --id R-HSA-69278 --output /tmp/diagram.png
python scripts/reactome_analysis.py diagram --id R-HSA-69278 --highlight TP53 --output /tmp/highlighted.png
python scripts/reactome_analysis.py diagram --id R-HSA-69278 --format svg --output /tmp/diagram.svg
python scripts/reactome_analysis.py reaction-diagram --id R-HSA-6804194 --output /tmp/rxn.png
```

### 11. Cross-Reference Mapping

Resolve identifiers to Reactome internal IDs and cross-references:

```bash
python scripts/reactome_analysis.py xref-mapping --id TP53 --output /tmp/xref.json
python scripts/reactome_analysis.py xref-mapping-batch --data "TP53,BRCA1" --output /tmp/xrefs.json
```

### 12. Search

```bash
python scripts/reactome_analysis.py search --query "TP53 apoptosis" --output /tmp/results.json
```

### 13. Query Entry by ID

```bash
python scripts/reactome_analysis.py query --id R-HSA-69278 --output /tmp/entry.json
```

### 14. Report & Species Comparison

```bash
python scripts/reactome_analysis.py report --token TOKEN --output /tmp/report.pdf
python scripts/reactome_analysis.py species-comparison --species-id 48892 --output /tmp/species.json
# Use --summary to truncate large output and avoid workspace file size limits
python scripts/reactome_analysis.py species-comparison --species-id 48892 --summary --output /tmp/species.json
```

## Recipe: Interpreting Gene Set Enrichment

A step-by-step workflow for interpreting gene set enrichment results:

1.  **Submit gene list** with projection to human pathways: `python
    scripts/reactome_analysis.py analyze-projection \ --data
    "TP53,BRCA1,EGFR,MYC,PTEN" --fdr 0.05 --output /tmp/enrichment.json`

2.  **Inspect top pathways** — examine `pathwaysFound`, top pathway names,
    p-values, and FDR values in the output.

3.  **Drill into a pathway** — get its sub-events and reaction details: `python
    run scripts/reactome_analysis.py contained-events --id R-HSA-69278 --output
    /tmp/steps.json python scripts/reactome_analysis.py participants --id
    <reaction_id> --output /tmp/parts.json`

4.  **Visualise** — export a diagram with your genes highlighted: `python
    scripts/reactome_analysis.py diagram --id R-HSA-69278 \ --highlight
    "TP53,BRCA1" --output /tmp/diagram.png`

5.  **Check hierarchy** — navigate up to see broader biological context: `bash
    python scripts/reactome_analysis.py event-ancestors --id R-HSA-69278
    --output /tmp/ancestors.json`

6.  **Cross-reference** — map identifiers to other databases: `python
    scripts/reactome_analysis.py xref-mapping --id TP53 --output
    /tmp/xrefs.json`

## Reference

For detailed API endpoint documentation, see
[references/api_reference.md](references/api_reference.md).
