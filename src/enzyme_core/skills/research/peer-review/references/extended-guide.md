# Extended guide

Read this reference when the task needs the advanced procedures, examples, or troubleshooting moved out of the core Skill instructions.

## Contents

- [Tone Calibration](#tone-calibration)
  - [Severity Language Guide](#severity-language-guide)
  - [Language Calibration Examples](#language-calibration-examples)
  - [Severity Thresholds](#severity-thresholds)
- [Score Calibration](#score-calibration)
  - [Scoring Principles](#scoring-principles)
  - [Score Floor Rules](#score-floor-rules)
  - [Scoring Reference Scale](#scoring-reference-scale)
  - [Sub-Score Dimensions (1-4 scale)](#sub-score-dimensions-1-4-scale)
- [Special Considerations by Manuscript Type](#special-considerations-by-manuscript-type)
  - [Original Research Articles](#original-research-articles)
  - [Reviews and Meta-Analyses](#reviews-and-meta-analyses)
  - [Methods Papers](#methods-papers)
  - [Short Reports/Letters](#short-reportsletters)
  - [Preprints](#preprints)
  - [Presentations and Slide Decks](#presentations-and-slide-decks)
- [Resources](#resources)
  - [references/reporting_standards.md](#referencesreportingstandardsmd)
  - [references/common_issues.md](#referencescommonissuesmd)
  - [references/scoring_rubric.md](#referencesscoringrubricmd)
  - [references/calibration_guidelines.md](#referencescalibrationguidelinesmd)
  - [references/paper_mechanics.md](#referencespapermechanicsmd)
- [Final Checklist](#final-checklist)

## Tone Calibration

Maintain calibrated, fair language that distinguishes between reporting issues and scientific misconduct.

### Severity Language Guide

| Situation | Use This | NOT This |
|-----------|----------|----------|
| Missing details | "needs clearer reporting" | "misleading" |
| Incomplete info | "would benefit from" | "fails to" |
| Scope limitation | "limited to [context]" | "flawed because" |
| Acknowledged limitation | "as authors note..." | (no penalty >1 point) |
| Different interpretation | "alternative explanation" | "contradictory" |

### Language Calibration Examples

**Too Harsh (Avoid):**
> "The authors misleadingly present their results without adequate baselines, fundamentally undermining the validity of all claims."

**Appropriately Calibrated:**
> "The baseline comparisons could be strengthened. Adding [specific baseline] would help contextualize the reported improvements (see Section 4.2, p. 8)."

### Severity Thresholds

- **Critical** [use sparingly, <5% of reviews]: Reserved for fundamental flaws that invalidate conclusions (fabricated data, inappropriate statistical tests hiding null results, undisclosed conflicts)
- **Major** [most significant issues]: Affects interpretation but potentially addressable (missing controls, statistical concerns, overclaiming)
- **Moderate** [typical issues]: Common limitations (scope restrictions, acknowledged missing baselines, reproducibility gaps)
- **Minor** [polish issues]: Presentation and clarity (typos, figure labels, citation format)

## Score Calibration

Calibrate scores to match human reviewer standards and avoid over-penalization.

### Scoring Principles

1. **Limitation Acknowledgment Bonus**: If authors explicitly acknowledge a limitation, do not penalize more than 1 point for that issue
2. **Reproducibility Credit**: Detailed methods + random seeds + confidence intervals → floor of 6-7
3. **Scope vs Quality**: A narrow-but-sound study = 6-7 (Weak Accept to Accept), not 5 (Borderline)
4. **Synthetic Data Reality**: Most ML papers use synthetic/benchmark data; this alone is not a major weakness

### Score Floor Rules

If ALL of these conditions are met, the overall score MUST be ≥6:
- Methodology is sound (even if limited in scope)
- Statistical reporting is rigorous (effect sizes, CIs, appropriate tests)
- Limitations are explicitly acknowledged
- Reproducibility details are provided (code/data availability, seeds, parameters)

### Scoring Reference Scale

| Score | Label | Description |
|-------|-------|-------------|
| 9-10 | Strong Accept | Significant contribution, excellent execution, high impact |
| 7-8 | Accept | Solid work, minor issues, clear contribution |
| 6 | Weak Accept | Sound methodology, acknowledged limitations, narrow scope acceptable |
| 5 | Borderline | Significant concerns but salvageable with major revisions |
| 3-4 | Reject | Fundamental issues with methodology or validity |
| 1-2 | Strong Reject | Major scientific or ethical concerns |

### Sub-Score Dimensions (1-4 scale)

- **Soundness**: Technical correctness of methodology and analysis
- **Originality**: Novelty of contribution relative to prior work
- **Clarity**: Quality of writing and presentation
- **Significance**: Potential impact on the field

See `references/scoring_rubric.md` for detailed scoring criteria and calibration examples.
See `references/calibration_guidelines.md` for calibration principles based on human reviewer alignment.

## Special Considerations by Manuscript Type

### Original Research Articles
- Emphasize rigor, reproducibility, and novelty
- Assess significance and impact
- Verify that conclusions are data-driven
- Check for complete methods and appropriate controls

### Reviews and Meta-Analyses
- Evaluate comprehensiveness of literature coverage
- Assess search strategy and inclusion/exclusion criteria
- Verify systematic approach and lack of bias
- Check for critical analysis vs. mere summarization
- For meta-analyses, evaluate statistical approach and heterogeneity

### Methods Papers
- Emphasize validation and comparison to existing methods
- Assess reproducibility and availability of protocols/code
- Evaluate improvements over existing approaches
- Check for sufficient detail for implementation

### Short Reports/Letters
- Adapt expectations for brevity
- Ensure core findings are still rigorous and significant
- Verify that format is appropriate for findings

### Preprints
- Recognize that these have not undergone formal peer review
- May be less polished than journal submissions
- Still apply rigorous standards for scientific validity
- Consider providing constructive feedback to help authors improve before journal submission

### Presentations and Slide Decks

**⚠️ CRITICAL: For presentations, NEVER read the PDF directly. ALWAYS convert to images first.**

When reviewing scientific presentations (PowerPoint, Beamer, slide decks):

#### Mandatory Image-Based Review Workflow

**NEVER attempt to read presentation PDFs directly** - this causes buffer overflow errors and doesn't show visual formatting issues.

**Required Process:**
1. Convert PDF to images using Python:
   ```bash
   python skills/scientific-slides/scripts/pdf_to_images.py presentation.pdf review/slide --dpi 150
   # Creates: review/slide-001.jpg, review/slide-002.jpg, etc.
   ```
2. Read and inspect EACH slide image file sequentially
3. Document issues with specific slide numbers
4. Provide feedback on visual formatting and content

**Print when starting review:**
```
[HH:MM:SS] PEER REVIEW: Presentation detected - converting to images for review
[HH:MM:SS] PDF REVIEW: NEVER reading PDF directly - using image-based inspection
```

#### Presentation-Specific Evaluation Criteria

**Visual Design and Readability:**
- [ ] Text is large enough (minimum 18pt, ideally 24pt+ for body text)
- [ ] High contrast between text and background (4.5:1 minimum, 7:1 preferred)
- [ ] Color scheme is professional and colorblind-accessible
- [ ] Consistent visual design across all slides
- [ ] White space is adequate (not cramped)
- [ ] Fonts are clear and professional

**Layout and Formatting (Check EVERY Slide Image):**
- [ ] No text overflow or truncation at slide edges
- [ ] No element overlaps (text over images, overlapping shapes)
- [ ] Titles are consistently positioned
- [ ] Content is properly aligned
- [ ] Bullets and text are not cut off
- [ ] Figures fit within slide boundaries
- [ ] Captions and labels are visible and readable

**Content Quality:**
- [ ] One main idea per slide (not overloaded)
- [ ] Minimal text (3-6 bullets per slide maximum)
- [ ] Bullet points are concise (5-7 words each)
- [ ] Figures are simplified and clear (not copy-pasted from papers)
- [ ] Data visualizations have large, readable labels
- [ ] Citations are present and properly formatted
- [ ] Results/data slides dominate the presentation (40-50% of content)

**Structure and Flow:**
- [ ] Clear narrative arc (introduction → methods → results → discussion)
- [ ] Logical progression between slides
- [ ] Slide count appropriate for talk duration (~1 slide per minute)
- [ ] Title slide includes authors, affiliation, date
- [ ] Introduction cites relevant background literature (3-5 papers)
- [ ] Discussion cites comparison papers (3-5 papers)
- [ ] Conclusions slide summarizes key findings
- [ ] Acknowledgments/funding slide at end

**Scientific Content:**
- [ ] Research question clearly stated
- [ ] Methods adequately summarized (not excessive detail)
- [ ] Results presented logically with clear visualizations
- [ ] Statistical significance indicated appropriately
- [ ] Conclusions supported by data shown
- [ ] Limitations acknowledged where appropriate
- [ ] Future directions or broader impact discussed

**Common Presentation Issues to Flag:**

**Critical Issues (Must Fix):**
- Text overflow making content unreadable
- Font sizes too small (<18pt)
- Element overlaps obscuring data
- Insufficient contrast (text hard to read)
- Figures too complex or illegible
- No citations (completely unsupported claims)
- Slide count drastically mismatched to duration

**Major Issues (Should Fix):**
- Inconsistent design across slides
- Too much text (walls of text, not bullets)
- Poorly simplified figures (axis labels too small)
- Cramped layout with insufficient white space
- Missing key structural elements (no conclusion slide)
- Poor color choices (not colorblind-safe)
- Minimal results content (<30% of slides)

**Minor Issues (Suggestions for Improvement):**
- Could use more visuals/diagrams
- Some slides slightly text-heavy
- Minor alignment inconsistencies
- Could benefit from more white space
- Additional citations would strengthen claims
- Color scheme could be more modern

#### Review Report Format for Presentations

**Summary Statement:**
- Overall impression of presentation quality
- Appropriateness for target audience and duration
- Key strengths (visual design, content, clarity)
- Key weaknesses (formatting issues, content gaps)
- Recommendation (ready to present, minor revisions, major revisions)

**Layout and Formatting Issues (By Slide Number):**
```
Slide 3: Text overflow - bullet point 4 extends beyond right margin
Slide 7: Element overlap - figure overlaps with caption text
Slide 12: Font size - axis labels too small to read from distance
Slide 18: Alignment - title not centered
```

**Content and Structure Feedback:**
- Adequacy of background context and citations
- Clarity of research question and objectives
- Quality of methods summary
- Effectiveness of results presentation
- Strength of conclusions and implications

**Design and Accessibility:**
- Overall visual appeal and professionalism
- Color contrast and readability
- Colorblind accessibility
- Consistency across slides

**Timing and Scope:**
- Whether slide count matches intended duration
- Appropriate level of detail for talk type
- Balance between sections

#### Example Image-Based Review Process

```
[14:30:00] PEER REVIEW: Starting review of presentation
[14:30:05] PEER REVIEW: Presentation detected - converting to images
[14:30:10] PDF REVIEW: Running pdf_to_images.py on presentation.pdf
[14:30:15] PDF REVIEW: Converted 25 slides to images in review/ directory
[14:30:20] PDF REVIEW: Inspecting slide 1/25 - title slide
[14:30:25] PDF REVIEW: Inspecting slide 2/25 - introduction
...
[14:35:40] PDF REVIEW: Inspecting slide 25/25 - acknowledgments
[14:35:45] PDF REVIEW: Completed image-based review
[14:35:50] PEER REVIEW: Found 8 layout issues, 3 content issues
[14:35:55] PEER REVIEW: Generating structured feedback by slide number
```

**Remember:** For presentations, the visual inspection via images is MANDATORY. Never attempt to read presentation PDFs as text - it will fail and miss all visual formatting issues.

## Resources

This skill includes reference materials to support comprehensive peer review:

### references/reporting_standards.md
Guidelines for major reporting standards across disciplines (CONSORT, PRISMA, ARRIVE, MIAME, STROBE, etc.) to evaluate completeness of methods and results reporting.

### references/common_issues.md
Catalog of frequent methodological and statistical issues encountered in peer review, with guidance on identifying and addressing them.

### references/scoring_rubric.md
Detailed scoring rubric with severity calibration, sub-dimension criteria (Soundness, Originality, Clarity, Significance), and score anchoring examples for consistent evaluation.

### references/calibration_guidelines.md
Human reviewer alignment guidelines based on empirical comparison with actual peer reviews. Contains calibration principles, common over-penalization patterns, and examples of appropriately calibrated feedback.

### references/paper_mechanics.md
Comprehensive checklist for paper presentation mechanics (tables, figures, notation, structure) based on issues frequently identified by human reviewers but sometimes missed by automated systems.

## Final Checklist

Before finalizing the review, verify:

**Content Evaluation:**
- [ ] Summary statement clearly conveys overall assessment
- [ ] Major concerns are clearly identified and justified
- [ ] Suggested revisions are specific and actionable
- [ ] Minor issues are noted but properly categorized
- [ ] Statistical methods have been evaluated
- [ ] Reproducibility and data availability assessed
- [ ] Ethical considerations verified
- [ ] Figures and tables evaluated for quality and integrity
- [ ] Writing quality assessed
- [ ] Review is thorough but proportionate to manuscript scope
- [ ] Recommendation is consistent with identified issues

**Paper Mechanics (Stage 5b):**
- [ ] Tables contain numeric values (not just "Higher"/"Better")
- [ ] Figures legible at 100% zoom
- [ ] Variables defined before use
- [ ] No section redundancy
- [ ] All figures/tables referenced in text

**Tone Calibration:**
- [ ] Language uses calibrated severity terms (see Severity Language Guide)
- [ ] Acknowledged limitations receive reduced penalty (≤1 point)
- [ ] "Critical" severity used only for fundamental flaws (<5% of reviews)
- [ ] Tone is constructive and professional throughout

**Score Calibration:**
- [ ] Score floor rules applied (sound + rigorous + acknowledged → ≥6)
- [ ] Scope limitations alone don't drop score below 6
- [ ] Sub-scores (Soundness, Originality, Clarity, Significance) are consistent with overall
- [ ] Score matches calibration examples for similar papers
