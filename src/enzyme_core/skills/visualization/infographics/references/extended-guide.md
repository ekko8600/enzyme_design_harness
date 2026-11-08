# Extended guide

Read this reference when the task needs the advanced procedures, examples, or troubleshooting moved out of the core Skill instructions.

## Contents

- [Smart Iterative Refinement](#smart-iterative-refinement)
  - [How It Works](#how-it-works)
  - [Quality Review Criteria](#quality-review-criteria)
  - [Review Log](#review-log)
- [Command-Line Reference](#command-line-reference)
  - [List All Options](#list-all-options)
- [Configuration](#configuration)
  - [API Key Setup](#api-key-setup)
- [Prompt Engineering Tips](#prompt-engineering-tips)
  - [Be Specific About Content](#be-specific-about-content)
  - [Include Data Points](#include-data-points)
  - [Specify Visual Elements](#specify-visual-elements)
- [Reference Files](#reference-files)
- [Troubleshooting](#troubleshooting)
  - [Common Issues](#common-issues)
- [Integration with Other Skills](#integration-with-other-skills)
- [Quick Reference Checklist](#quick-reference-checklist)

## Smart Iterative Refinement

### How It Works

```
┌─────────────────────────────────────────────────────┐
│  1. Generate infographic with Nano Banana Pro       │
│                    ↓                                │
│  2. Review quality with Gemini 3 Pro                │
│                    ↓                                │
│  3. Score >= threshold?                             │
│       YES → DONE! (early stop)                      │
│       NO  → Improve prompt, go to step 1            │
│                    ↓                                │
│  4. Repeat until quality met OR max iterations      │
└─────────────────────────────────────────────────────┘
```

### Quality Review Criteria

Gemini 3 Pro evaluates each infographic on:

1. **Visual Hierarchy & Layout** (0-2 points)
   - Clear visual hierarchy
   - Logical reading flow
   - Balanced composition

2. **Typography & Readability** (0-2 points)
   - Readable text
   - Bold headlines
   - No overlapping

3. **Data Visualization** (0-2 points)
   - Prominent numbers
   - Clear charts/icons
   - Proper labels

4. **Color & Accessibility** (0-2 points)
   - Professional colors
   - Sufficient contrast
   - Colorblind-friendly

5. **Overall Impact** (0-2 points)
   - Professional appearance
   - Free of visual bugs
   - Achieves communication goal

### Review Log

Each generation produces a JSON review log:
```json
{
  "user_prompt": "5 benefits of exercise...",
  "infographic_type": "list",
  "style": "healthcare",
  "doc_type": "marketing",
  "quality_threshold": 8.5,
  "iterations": [
    {
      "iteration": 1,
      "image_path": "figures/exercise_v1.png",
      "score": 8.7,
      "needs_improvement": false,
      "critique": "SCORE: 8.7\nSTRENGTHS:..."
    }
  ],
  "final_score": 8.7,
  "early_stop": true,
  "early_stop_reason": "Quality score 8.7 meets threshold 8.5"
}
```

---

## Command-Line Reference

```bash
python skills/infographics/scripts/generate_infographic.py [OPTIONS] PROMPT

Arguments:
  PROMPT                    Description of the infographic content

Options:
  -o, --output PATH         Output file path (required)
  -t, --type TYPE           Infographic type preset
  -s, --style STYLE         Industry style preset
  -p, --palette PALETTE     Colorblind-safe palette
  -b, --background COLOR    Background color (default: white)
  --doc-type TYPE           Document type for quality threshold
  --iterations N            Maximum refinement iterations (default: 3)
  --api-key KEY             OpenRouter API key
  -v, --verbose             Verbose output
  --list-options            List all available options
```

### List All Options

```bash
python skills/infographics/scripts/generate_infographic.py --list-options
```

---

## Configuration

### API Key Setup

Set your OpenRouter API key:
```bash
export OPENROUTER_API_KEY='your_api_key_here'
```

Get an API key at: https://openrouter.ai/keys

---

## Prompt Engineering Tips

### Be Specific About Content

✓ **Good prompts** (specific, detailed):
```
"5 benefits of meditation: reduces stress, improves focus, 
better sleep, lower blood pressure, emotional balance"
```

✗ **Avoid vague prompts**:
```
"meditation infographic"
```

### Include Data Points

✓ **Good**:
```
"Market growth from $10B (2020) to $45B (2025), CAGR 35%"
```

✗ **Vague**:
```
"market is growing"
```

### Specify Visual Elements

✓ **Good**:
```
"Timeline showing 5 milestones with icons for each event"
```

---

## Reference Files

For detailed guidance, load these reference files:

- **`references/infographic_types.md`**: Extended templates for all 10+ types
- **`references/design_principles.md`**: Visual hierarchy, layout, typography
- **`references/color_palettes.md`**: Full palette specifications

---

## Troubleshooting

### Common Issues

**Problem**: Text in infographic is unreadable
- **Solution**: Reduce text content; use --type to specify layout type

**Problem**: Colors clash or are inaccessible
- **Solution**: Use `--palette wong` for colorblind-safe colors

**Problem**: Quality score too low
- **Solution**: Increase iterations with `--iterations 3`; use more specific prompt

**Problem**: Wrong infographic type generated
- **Solution**: Always specify `--type` flag for consistent results

---

## Integration with Other Skills

This skill works synergistically with:

- **scientific-schematics**: For technical diagrams and flowcharts
- **market-research-reports**: Infographics for business reports
- **scientific-slides**: Infographic elements for presentations
- **generate-image**: For non-infographic visual content

---

## Quick Reference Checklist

Before generating:
- [ ] Clear, specific content description
- [ ] Infographic type selected (`--type`)
- [ ] Style appropriate for audience (`--style`)
- [ ] Output path specified (`-o`)
- [ ] API key configured

After generating:
- [ ] Review the generated image
- [ ] Check the review log for scores
- [ ] Regenerate with more specific prompt if needed

---

Use this skill to create professional, accessible, and visually compelling infographics using the power of Nano Banana Pro AI with intelligent quality review.
