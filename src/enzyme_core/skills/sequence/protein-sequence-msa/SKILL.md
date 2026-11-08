---
name: protein-sequence-msa
description: 'Prepares protein multiple sequence alignments for conservation analysis
  and MSA-consuming models. Prefer high-quality ColabFold/Boltz-style MMseqs2 A3M
  output when homolog search is needed, and use EBI Clustal Omega mainly as a fallback
  for already collected FASTA homologs. Do not use to align non-protein sequences,
  perform structural alignment, or invent conservation metrics.

  '
metadata:
  category: sequence
  legacy_category: utilities
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - sequence.analysis
---

## Prerequisites

1.  **Python**: Run scripts directly with `python`. `python` is optional, not
    required, because the scripts bootstrap local shared helpers.
2.  **User Notification**: If LICENSE_NOTIFICATION.txt does not already exist in
    this skill directory then (1) prominently notify the user to check the terms
    at https://www.ebi.ac.uk/jdispatcher/msa/clustalo and
    https://www.ebi.ac.uk/about/terms-of-use/, then (2) create the file
    recording the notification text and timestamp.
3.  **`.env` file**: Make sure the `.env` file exists in your home directory.
    Create one if it does not exist.
4.  **`USER_EMAIL`** (optional but recommended): Recommended by the EBI for
    Clustal Omega job tracking, but the skill works without it. If the variable
    is missing from `.env`, do NOT ask the user to paste it into the chat (this
    would leak the value into the agent's context). Instead, give the user this
    command — **substituting `ENV_FILE` with the resolved literal path to the
    `.env` file**:

    ```bash
    printf "Enter contact email: " && read email && echo "USER_EMAIL=$email" >> "ENV_FILE" && echo "Saved."
    ```

    The scripts load credentials automatically via `dotenv`. **NEVER** read,
    print, or inspect the `.env` file or its variables (e.g. no `cat`, `grep`,
    `echo`, `printenv`, or `os.environ.get` on keys). Credentials must stay out
    of the agent's context.

## Core Rules

-   **Default MSA method**: `msa_align.py` now defaults to **boltz (ColabFold
    MMseqs2)** for single-query MSA search, falling back to **EBI Clustal
    Omega** for pre-collected multi-sequence alignments. Use `--method boltz` or
    `--method ebi` to force a specific backend.
-   **Use the Wrapper**: ALWAYS execute the alignment using
    `scripts/msa_align.py` rather than writing your own curl or custom Python
    requests.  The script routes to boltz first, then EBI as fallback.
-   **Always state the method:** Every report must clearly state which MSA
    backend was used (boltz/ColabFold MMseqs2 or EBI Clustal Omega).
-   **No Hallucinations**: Do NOT invent alignments or conservation metrics.
    Report only what is present in the alignment file.

## Goal

Take a protein query sequence (single or FASTA) and produce a high-quality
multiple sequence alignment.  By default, uses boltz's built-in ColabFold
MMseqs2 client (`boltz.data.msa.mmseqs2`) to search UniRef + environmental
databases and return cleaned A3M output suitable for GEMME, conservation
analysis, and PLM handoff.  Falls back to EBI Clustal Omega for pre-collected
multi-sequence FASTA inputs.

## Instructions

1.  **Prepare Input:** For a single query sequence, provide it inline or as a
    `@file.fasta` with one sequence. For multiple pre-collected sequences,
    provide a FASTA file with 2+ entries.

2.  **Execute Alignment** (auto-detect backend):
    ```bash
    python scripts/msa_align.py <INPUT_FASTA> -o <OUTPUT_FILE>
    ```
    - Single query → boltz (ColabFold MMseqs2) searches UniRef + MGnify
    - Multi-sequence FASTA → EBI Clustal Omega fallback
    - Force backend: `--method boltz` or `--method ebi`

2a. **Standalone boltz MSA search** (single query, no Clustal dependency):
    ```bash
    python scripts/msa_boltz_search.py <SEQUENCE> -o <OUT_DIR>
    ```
    Produces `combined_cleaned.a3m` directly from the ColabFold API.
    Use `--keep-intermediates` to retain raw UniRef/env A3M files.

2a. **Prepare A2M-like output for GEMME if needed:** Clustal Omega returns
    aligned FASTA, and MMseqs2/Boltz/ColabFold-style search tools may return
    A3M or A2M.
    When a downstream model requires GEMME-compatible A2M-like FASTA with the
    query sequence first, normalize the alignment with:

    ```bash
    python scripts/msa_to_a2m.py <ALIGNMENT_FILE> \
      --query-sequence <UNGAPPED_WILD_TYPE_SEQUENCE> \
      --msa-start 1 \
      --drop-lowercase-inserts \
      -o <GEMME_INPUT.a2m>
    ```

    Use `--query-id` instead of `--query-sequence` when the target header is
    known. Set `--msa-start` and `--msa-end` when the alignment covers only a
    subsequence. Use `--drop-lowercase-inserts` for A3M inputs; lowercase A3M
    residues are insert states and must not shift GEMME or conservation columns.
    This conversion does not perform a homology search or create new alignment
    columns; it only reorders and normalizes an existing alignment for
    downstream MSA-consuming models.

2b. **High-quality MSA for design/GEMME (default path):** Use `msa_align.py`
    with a single query sequence — it will automatically route through boltz's
    ColabFold MMseqs2 client, producing `combined_cleaned.a3m` with UniRef +
    MGnify/environmental expansion.  This is the recommended default for
    rational design, GEMME, and conservation analysis.

    To run the search standalone:
    ```bash
    python scripts/msa_boltz_search.py "MGSTWGSP..." -o msa_out/
    ```

    The boltz-based path replaces the older MMseqs2 local-database approach
    and the `mmseqs2_search.py`/EBI Clustal path for single-query MSA.
    Prefer `combined_cleaned.a3m` for GEMME and conservation handoff.

3.  **Interpret and Report Results:** Analyze the Clustal Omega alignment by
    selecting metrics and mapping strategies aligned with the research
    objective. Note that while Clustal Omega produces a Global Alignment,
    pairwise metrics can be extracted to evaluate specific relationships within
    the set:

    *   **Identity Metric Options:** The choice of denominator determines how
        insertions/deletions (gaps) affect the final percentage. Select the most
        appropriate calculation based on the biological context:
        *   **Pairwise - Sequence Coverage:** `(Identical Residue Matches) /
            (Length of Shorter Sequence)`. Use when determining if a specific
            domain or fragment is fully preserved within a larger protein. This
            ignores gaps in the longer sequence, focusing purely on the
            "content" of the shorter one.
        *   **Pairwise - Global Identity:** `(Identical Residue Matches) /
            (Total Alignment Columns)`. Use when comparing full-length sequences
            of similar expected length. This is the most conservative metric; it
            penalizes for all gaps (indels) introduced by any sequence in the
            MSA.
        *   **Pairwise - Overlap Identity:** `(Identical Residue Matches) /
            (Total Alignment Columns - Terminal Gaps)`. Use when comparing a
            fragment to a full-length protein or when sequences have long
            unaligned "tails." This focuses on similarity only where the
            sequences physically overlap.
        *   **Multisequence - Conservation Index:** `(Fully Conserved Columns) /
            (Total Alignment Columns)`. Use for quantifying the percentage of
            residues that are 100% identical across the entire alignment set.
            This identifies the core evolutionary signature of the protein
            family.
    *   **Feature Mapping:** Leverage known biological data from specific
        sequences to ground the analysis:
        *   **Knowledge Gathering:** Identify relevant known sites or regions
            (e.g., catalytic residues, binding motifs) from your input or via
            external tools.
        *   **Coordinate Projection:** Map these features onto the corresponding
            Column Indices of the alignment.
        *   **Targeted Discussion:** Use these columns to drive the assessment:
            *   **Local Conservation:** Analyze if the known functional residues
                are invariant across the set.
            *   **Region-Specific Metrics:** Calculate identity/similarity
                specifically within the mapped functional regions rather than
                the whole sequence.
            *   **Goal Contribution:** Discuss how this data contributes to your
                goal, e.g. using conservation to corroborate a prediction or
                divergence to reject a functional hypothesis.

## References

-   Multiple Sequence Alignment: https://www.ebi.ac.uk/jdispatcher/msa/clustalo
