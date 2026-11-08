---
name: literature-protein-collector
description: Search multiple scholarly sources for a named target protein and sequence,
  fetch only lawful open-access content, support evidence-bounded agent reading, extract
  reported mutants and assay results, map paper numbering to the target sequence,
  and produce a traceable mutants.csv plus mechanism and design reports. Use for enzyme-engineering
  literature mining, known-mutation collection, catalytic-mechanism review, and sequence-aware
  reconciliation across homologs or constructs.
metadata:
  category: research
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - science.general_operation
---

# Literature protein collector

Build a reproducible literature-to-mutation evidence package for one target
protein. The scripts own deterministic retrieval, caching, deduplication,
sequence alignment, validation, and report generation. The host agent owns
relevance decisions, close reading, entity resolution, and scientific
interpretation.

## Non-negotiable boundaries

- Fetch and retain only content that is lawfully open access.
- Never bypass a paywall, use Sci-Hub, or scrape restricted full text.
- If full text is unavailable, analyze the accessible title, abstract,
  metadata, captions, or partial HTML and label the actual evidence basis.
- Never invent a sequence, mutation, assay value, source locator, or citation.
- Preserve each paper's own full or partial protein sequence. Do not replace it
  with the user's target sequence.
- Map paper coordinates to target coordinates only through the supplied
  alignment code. Preserve failed or ambiguous mappings.
- Treat database records and paper claims as source evidence, not independent
  experimental validation.

## Outputs

Produce:

1. `output/candidates.json`: deduplicated search metadata.
2. `output/selected.json`: the agent-filtered subset, retaining the same schema.
3. `output/raw_data.json` plus `output/fulltext/`: retrieval status and lawful
   cached content.
4. `output/analysis.json`: agent-authored structured findings and mutants.
5. `output/mutants.csv`: paper coordinates, target-coordinate mappings,
   assays, evidence locators, confidence, and verification status.
6. `output/report.md`: retrieval summary, mechanism synthesis, per-paper
   findings, cross-paper position summary, limitations, and design insights.

## Credentials and configuration

The core search and report path uses the Python standard library. Copy
`config.example.json` to an ignored, task-local `config.json` only when a
configuration file is necessary; prefer environment variables or a secure
user-supplied `--config` path:

- `UNPAYWALL_EMAIL` for responsible Unpaywall access;
- `NCBI_API_KEY` for higher NCBI request limits;
- `OPENALEX_API_KEY` and `SEMANTIC_SCHOLAR_API_KEY` for optional service keys;
- `CORE_API_KEY` to enable CORE;
- `PADDLEOCR_VL_TOKEN` for optional PDF OCR.

Obtain credentials through the host's secure credential mechanism. Never
hardcode or echo a secret. Do not install packages automatically; OCR alone
requires the optional `requests` package.

## Workflow

### 1. Check source connectivity when needed

Use this diagnostic on a new machine or when results are unexpectedly sparse:

```bash
python3 scripts/literature_collector.py check-sources
```

The probe is not literature evidence and must not be cited.

### 2. Search multiple sources

```bash
python3 scripts/literature_collector.py search \
  --query "GH11 xylanase thermostability mutant" \
  --query "endo-1,4-beta-xylanase directed evolution" \
  --sources europepmc,openalex,pubmed,semanticscholar,crossref,arxiv \
  --max-results 25 \
  --output output/candidates.json
```

Pass multiple `--query` values to one process so the built-in rate limiting,
caching, and deduplication remain effective. To combine previous searches:

```bash
python3 scripts/literature_collector.py merge-candidates \
  --inputs output/candidates.json output/candidates_round2.json \
  --output output/candidates.json
```

### 3. Pre-filter candidates

Read titles, abstracts, identifiers, source categories, organisms, and target
context. Retain likely target, homolog, mechanism, mutation, directed-evolution,
or assay papers. Write the result to `output/selected.json` without changing
the top-level search schema.

### 4. Fetch lawful open-access content

```bash
python3 scripts/literature_collector.py fetch-fulltext \
  --selected output/selected.json \
  --output output/raw_data.json \
  --fulltext-dir output/fulltext \
  --concurrency 10
```

The resolver tries lawful sources such as Europe PMC, declared OA links,
Unpaywall, CORE, and DOAJ. It records `text`, `pdf`, or `abstract_only` rather
than fabricating a fallback. Inspect `fulltext_summary` before reading.

### 5. Read and extract evidence

For every selected paper, record:

- `analysis_basis`: `full_text`, `partial_html`, `abstract_only`, or
  `metadata_only`;
- `accessible_content_summary` and `content_access_note`;
- `findings[]` with `claim`, `evidence_type`, `source_locator`, `confidence`,
  and `limitations`;
- the paper's `paper_sequence` and `sequence_source_note` when available;
- `mutants[]` with the reported mutation, assay, result, method, locator,
  evidence type, confidence, and limitations.

Allowed finding evidence types are `direct_observation`,
`author_interpretation`, and `agent_inference`. Allowed confidence values are
`high`, `medium`, and `low`.

When a field is uncertain, preserve it with a literal `[uncertain]` prefix.
Do not drop a useful but incomplete record and do not upgrade it to certainty.

For PDFs with dense mutation or kinetic tables, prefer OCR before extracting
numbers:

```bash
python3 scripts/ocr_pdf.py \
  --file output/fulltext/c3.pdf \
  --output output/fulltext_ocr/c3.ocr.md
```

Batch OCR is resumable:

```bash
python3 scripts/ocr_pdf.py \
  --dir output/fulltext \
  --output-dir output/fulltext_ocr
```

### 6. Use parallel reading when the host supports it

Assign one paper or a small paper batch per independent reader. Each reader
writes one JSON object to
`output/analysis_fragments/<candidate_id>.json`. After all fragments exist,
create `output/synthesis.json` with the target identity, sequence, mechanism
synthesis, and design implications, then merge:

```bash
python3 scripts/literature_collector.py merge-analysis \
  --fragments-dir output/analysis_fragments \
  --synthesis output/synthesis.json \
  --selected output/selected.json \
  --output output/analysis.json
```

The selected-file check reports missing fragments, allowing a bounded resume.
If parallel dispatch is unavailable, create the same `analysis.json` serially.

### 7. Map positions and render final artifacts

```bash
python3 scripts/literature_collector.py from-analysis \
  --analysis output/analysis.json \
  --output-csv output/mutants.csv \
  --output-report output/report.md \
  --search-summary output/raw_data.json
```

For a small standalone coordinate check:

```bash
python3 scripts/literature_collector.py align \
  --paper-seq "MKTAYIAKQR..." \
  --target-seq "MKTAYIAKQR..." \
  --pos "45,23,10"
```

## Minimum analysis contract

`output/analysis.json` must contain the target name, target sequence,
mechanism/design synthesis, and a `papers` array. Every paper entry must contain
its citation or identifier, retrieval status, evidence basis, accessible-content
summary, findings, paper sequence if available, and mutant records.

`mutation_position` is always the 1-based position in that paper's reported
sequence. The script calculates `mapped_position_in_target_seq`. If the paper
does not disclose a usable sequence, leave it empty and state that coordinate
mapping and wild-type validation are unavailable.

When the paper explicitly reports the wild-type residue, record it so the
script can detect contradictions. If it is omitted but the paper sequence is
available, the script may auto-fill it and mark that action explicitly.

## Completion criteria

Call the collection complete only when:

- all selected papers have a structured record, including abstract-only ones;
- every scientific claim has a citation and source locator appropriate to the
  accessible material;
- inaccessible content and extraction limitations are explicit;
- paper sequences and numbering are preserved without silent substitution;
- coordinate mapping and wild-type checks have been run or marked unavailable;
- `mutants.csv` and `report.md` are non-empty and internally consistent;
- no restricted full text, secret, or invented evidence is present.

## Resources

- Read `reference.md` for source-specific API behavior, rate limits, caches,
  retrieval fallbacks, and troubleshooting.
- Read `examples.md` for representative searches and structured output
  fragments.
