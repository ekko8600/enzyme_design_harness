# Extended guide

Read this reference when the task needs the advanced procedures, examples, or troubleshooting moved out of the core Skill instructions.

## Contents

- [Best Practices](#best-practices)
  - [Prioritizing High-Impact Papers (CRITICAL)](#prioritizing-high-impact-papers-critical)
  - [Search Strategy](#search-strategy)
  - [Screening and Selection](#screening-and-selection)
  - [Synthesis](#synthesis)
  - [Quality and Reproducibility](#quality-and-reproducibility)
  - [Writing](#writing)
- [Common Pitfalls to Avoid](#common-pitfalls-to-avoid)
- [Example Workflow](#example-workflow)
- [Integration with Other Skills](#integration-with-other-skills)
  - [Database Access Skills](#database-access-skills)
  - [Analysis Skills](#analysis-skills)
  - [Visualization Skills](#visualization-skills)
  - [Writing Skills](#writing-skills)
  - [Venue-Specific Writing Styles](#venue-specific-writing-styles)
- [Resources](#resources)
  - [Bundled Resources](#bundled-resources)
  - [External Resources](#external-resources)
- [Dependencies](#dependencies)
  - [Required Python Packages](#required-python-packages)
  - [Required System Tools](#required-system-tools)
- [Summary](#summary)

## Best Practices

### Prioritizing High-Impact Papers (CRITICAL)

**Always prioritize influential, highly-cited papers from reputable authors and top venues.** Quality matters more than quantity in literature reviews.

#### Citation Count Thresholds

Use citation counts to identify the most impactful papers:

| Paper Age | Citation Threshold | Classification |
|-----------|-------------------|----------------|
| 0-3 years | 20+ citations | Noteworthy |
| 0-3 years | 100+ citations | Highly Influential |
| 3-7 years | 100+ citations | Significant |
| 3-7 years | 500+ citations | Landmark Paper |
| 7+ years | 500+ citations | Seminal Work |
| 7+ years | 1000+ citations | Foundational |

#### Journal and Venue Tiers

Prioritize papers from higher-tier venues:

- **Tier 1 (Always Prefer):** Nature, Science, Cell, NEJM, Lancet, JAMA, PNAS, Nature Medicine, Nature Biotechnology
- **Tier 2 (Strong Preference):** High-impact specialized journals (IF>10), top conferences (NeurIPS, ICML for ML/AI)
- **Tier 3 (Include When Relevant):** Respected specialized journals (IF 5-10)
- **Tier 4 (Use Sparingly):** Lower-impact peer-reviewed venues

#### Author Reputation Assessment

Prefer papers from:
- **Senior researchers** with high h-index (>40 in established fields)
- **Leading research groups** at recognized institutions (Harvard, Stanford, MIT, Oxford, etc.)
- **Authors with multiple Tier-1 publications** in the relevant field
- **Researchers with recognized expertise** (awards, editorial positions, society fellows)

#### Identifying Seminal Papers

For any topic, identify foundational work by:
1. **High citation count** (typically 500+ for papers 5+ years old)
2. **Frequently cited by other included studies** (appears in many reference lists)
3. **Published in Tier-1 venues** (Nature, Science, Cell family)
4. **Written by field pioneers** (often cited as establishing concepts)

### Search Strategy
1. **Use multiple databases** (minimum 3): Ensures comprehensive coverage
2. **Include preprint servers**: Captures latest unpublished findings
3. **Document everything**: Search strings, dates, result counts for reproducibility
4. **Test and refine**: Run pilot searches, review results, adjust search terms
5. **Sort by citations**: When available, sort search results by citation count to surface influential work first

### Screening and Selection
1. **Use clear criteria**: Document inclusion/exclusion criteria before screening
2. **Screen systematically**: Title → Abstract → Full text
3. **Document exclusions**: Record reasons for excluding studies
4. **Consider dual screening**: For systematic reviews, have two reviewers screen independently
5. **Prioritize Tier-1 venues**: Include all relevant papers from top venues before considering lower-tier sources

### Synthesis
1. **Organize thematically**: Group by themes, NOT by individual studies
2. **Synthesize across studies**: Compare, contrast, identify patterns
3. **Be critical**: Evaluate quality and consistency of evidence
4. **Identify gaps**: Note what's missing or understudied
5. **Lead with high-impact work**: Start each theme with the most influential/cited papers

### Quality and Reproducibility
1. **Assess study quality**: Use appropriate quality assessment tools
2. **Verify all citations**: Run verify_citations.py script
3. **Document methodology**: Provide enough detail for others to reproduce
4. **Follow guidelines**: Use PRISMA for systematic reviews

### Writing
1. **Be objective**: Present evidence fairly, acknowledge limitations
2. **Be systematic**: Follow structured template
3. **Be specific**: Include numbers, statistics, effect sizes where available
4. **Be clear**: Use clear headings, logical flow, thematic organization
5. **Cite impact indicators**: When relevant, mention citation counts and venue prestige

## Common Pitfalls to Avoid

1. **Single database search**: Misses relevant papers; always search multiple databases
2. **No search documentation**: Makes review irreproducible; document all searches
3. **Study-by-study summary**: Lacks synthesis; organize thematically instead
4. **Unverified citations**: Leads to errors; always run verify_citations.py
5. **Too broad search**: Yields thousands of irrelevant results; refine with specific terms
6. **Too narrow search**: Misses relevant papers; include synonyms and related terms
7. **Ignoring preprints**: Misses latest findings; include bioRxiv, medRxiv, arXiv
8. **No quality assessment**: Treats all evidence equally; assess and report quality
9. **Publication bias**: Only positive results published; note potential bias
10. **Outdated search**: Field evolves rapidly; clearly state search date

## Example Workflow

Complete workflow for a biomedical literature review:

```bash
# 1. Create review document from template
cp assets/review_template.md crispr_sickle_cell_review.md

# 2. Search multiple databases using appropriate skills
# - Use gget skill for PubMed, bioRxiv
# - Use direct API access for arXiv, Semantic Scholar
# - Export results in JSON format

# 3. Aggregate and process results
python scripts/search_databases.py combined_results.json \
  --deduplicate \
  --rank citations \
  --year-start 2015 \
  --year-end 2024 \
  --format markdown \
  --output search_results.md \
  --summary

# 4. Screen results and extract data
# - Manually screen titles, abstracts, full texts
# - Extract key data into the review document
# - Organize by themes

# 5. Write the review following template structure
# - Introduction with clear objectives
# - Detailed methodology section
# - Results organized thematically
# - Critical discussion
# - Clear conclusions

# 6. Verify all citations
python scripts/verify_citations.py crispr_sickle_cell_review.md

# Review the citation report
cat crispr_sickle_cell_review_citation_report.json

# Fix any failed citations and re-verify
python scripts/verify_citations.py crispr_sickle_cell_review.md

# 7. Generate professional PDF
python scripts/generate_pdf.py crispr_sickle_cell_review.md \
  --citation-style nature \
  --output crispr_sickle_cell_review.pdf

# 8. Review final PDF and markdown outputs
```

## Integration with Other Skills

This skill works seamlessly with other scientific skills:

### Database Access Skills
- **gget**: PubMed, bioRxiv, COSMIC, AlphaFold, Ensembl, UniProt
- **bioservices**: ChEMBL, KEGG, Reactome, UniProt, PubChem
- **datacommons-client**: Demographics, economics, health statistics

### Analysis Skills
- **pydeseq2**: RNA-seq differential expression (for methods sections)
- **scanpy**: Single-cell analysis (for methods sections)
- **anndata**: Single-cell data (for methods sections)
- **biopython**: Sequence analysis (for background sections)

### Visualization Skills
- **matplotlib**: Generate figures and plots for review
- **seaborn**: Statistical visualizations

### Writing Skills
- **brand-guidelines**: Apply institutional branding to PDF
- **internal-comms**: Adapt review for different audiences
- **venue-templates**: Access venue-specific writing style guides when preparing reviews for publication

### Venue-Specific Writing Styles

When preparing a literature review for a specific journal, consult the **venue-templates** skill for writing style guidance:
- `venue_writing_styles.md`: Master style comparison across venues
- `nature_science_style.md`: Nature/Science flowing abstract style, story-driven structure
- `cell_press_style.md`: Cell Press graphical abstracts, Highlights format
- `medical_journal_styles.md`: NEJM/Lancet/JAMA structured abstracts, PRISMA compliance

These guides help adapt your review's tone, abstract format, and structure to match the target venue's expectations.

## Resources

### Bundled Resources

**Scripts:**
- `scripts/verify_citations.py`: Verify DOIs and generate formatted citations
- `scripts/generate_pdf.py`: Convert markdown to professional PDF
- `scripts/search_databases.py`: Process, deduplicate, and format search results

**References:**
- `references/citation_styles.md`: Detailed citation formatting guide (APA, Nature, Vancouver, Chicago, IEEE)
- `references/database_strategies.md`: Comprehensive database search strategies

**Assets:**
- `assets/review_template.md`: Complete literature review template with all sections

### External Resources

**Guidelines:**
- PRISMA (Systematic Reviews): http://www.prisma-statement.org/
- Cochrane Handbook: https://training.cochrane.org/handbook
- AMSTAR 2 (Review Quality): https://amstar.ca/

**Tools:**
- MeSH Browser: https://meshb.nlm.nih.gov/search
- PubMed Advanced Search: https://pubmed.ncbi.nlm.nih.gov/advanced/
- Boolean Search Guide: https://www.ncbi.nlm.nih.gov/books/NBK3827/

**Citation Styles:**
- APA Style: https://apastyle.apa.org/
- Nature Portfolio: https://www.nature.com/nature-portfolio/editorial-policies/reporting-standards
- NLM/Vancouver: https://www.nlm.nih.gov/bsd/uniform_requirements.html

## Dependencies

### Required Python Packages
```bash
pip install requests  # For citation verification
```

### Required System Tools
```bash
# For PDF generation
brew install pandoc  # macOS
apt-get install pandoc  # Linux

# For LaTeX (PDF generation)
brew install --cask mactex  # macOS
apt-get install texlive-xetex  # Linux
```

Check dependencies:
```bash
python scripts/generate_pdf.py --check-deps
```

## Summary

This literature-review skill provides:

1. **Systematic methodology** following academic best practices
2. **Multi-database integration** via existing scientific skills
3. **Citation verification** ensuring accuracy and credibility
4. **Professional output** in markdown and PDF formats
5. **Comprehensive guidance** covering the entire review process
6. **Quality assurance** with verification and validation tools
7. **Reproducibility** through detailed documentation requirements

Conduct thorough, rigorous literature reviews that meet academic standards and provide comprehensive synthesis of current knowledge in any domain.
