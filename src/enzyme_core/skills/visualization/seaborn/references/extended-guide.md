# Extended guide

Read this reference when the task needs the advanced procedures, examples, or troubleshooting moved out of the core Skill instructions.

## Contents

- [Theming and Aesthetics](#theming-and-aesthetics)
  - [Set Theme](#set-theme)
  - [Styles](#styles)
  - [Contexts](#contexts)
- [Best Practices](#best-practices)
  - [1. Data Preparation](#1-data-preparation)
  - [2. Choose the Right Plot Type](#2-choose-the-right-plot-type)
  - [3. Use Figure-Level Functions for Faceting](#3-use-figure-level-functions-for-faceting)
  - [4. Leverage Semantic Mappings](#4-leverage-semantic-mappings)
  - [5. Control Statistical Estimation](#5-control-statistical-estimation)
  - [6. Combine with Matplotlib](#6-combine-with-matplotlib)
  - [7. Save High-Quality Figures](#7-save-high-quality-figures)
- [Common Patterns](#common-patterns)
  - [Exploratory Data Analysis](#exploratory-data-analysis)
  - [Publication-Quality Figures](#publication-quality-figures)
  - [Complex Multi-Panel Figures](#complex-multi-panel-figures)
  - [Time Series with Confidence Bands](#time-series-with-confidence-bands)
- [Troubleshooting](#troubleshooting)
  - [Issue: Legend Outside Plot Area](#issue-legend-outside-plot-area)
  - [Issue: Overlapping Labels](#issue-overlapping-labels)
  - [Issue: Figure Too Small](#issue-figure-too-small)
  - [Issue: Colors Not Distinct Enough](#issue-colors-not-distinct-enough)
  - [Issue: KDE Too Smooth or Jagged](#issue-kde-too-smooth-or-jagged)
- [Resources](#resources)
  - [references/](#references)

## Theming and Aesthetics

### Set Theme

`set_theme()` controls overall appearance:

```python
# Set complete theme
sns.set_theme(style='whitegrid', palette='pastel', font='sans-serif')

# Reset to defaults
sns.set_theme()
```

### Styles

Control background and grid appearance:
- `"darkgrid"` - Gray background with white grid (default)
- `"whitegrid"` - White background with gray grid
- `"dark"` - Gray background, no grid
- `"white"` - White background, no grid
- `"ticks"` - White background with axis ticks

```python
sns.set_style("whitegrid")

# Remove spines
sns.despine(left=False, bottom=False, offset=10, trim=True)

# Temporary style
with sns.axes_style("white"):
    sns.scatterplot(data=df, x='x', y='y')
```

### Contexts

Scale elements for different use cases:
- `"paper"` - Smallest (default)
- `"notebook"` - Slightly larger
- `"talk"` - Presentation slides
- `"poster"` - Large format

```python
sns.set_context("talk", font_scale=1.2)

# Temporary context
with sns.plotting_context("poster"):
    sns.barplot(data=df, x='category', y='value')
```

## Best Practices

### 1. Data Preparation

Always use well-structured DataFrames with meaningful column names:

```python
# Good: Named columns in DataFrame
df = pd.DataFrame({'bill': bills, 'tip': tips, 'day': days})
sns.scatterplot(data=df, x='bill', y='tip', hue='day')

# Avoid: Unnamed arrays
sns.scatterplot(x=x_array, y=y_array)  # Loses axis labels
```

### 2. Choose the Right Plot Type

**Continuous x, continuous y:** `scatterplot`, `lineplot`, `kdeplot`, `regplot`
**Continuous x, categorical y:** `violinplot`, `boxplot`, `stripplot`, `swarmplot`
**One continuous variable:** `histplot`, `kdeplot`, `ecdfplot`
**Correlations/matrices:** `heatmap`, `clustermap`
**Pairwise relationships:** `pairplot`, `jointplot`

### 3. Use Figure-Level Functions for Faceting

```python
# Instead of manual subplot creation
sns.relplot(data=df, x='x', y='y', col='category', col_wrap=3)

# Not: Creating subplots manually for simple faceting
```

### 4. Leverage Semantic Mappings

Use `hue`, `size`, and `style` to encode additional dimensions:

```python
sns.scatterplot(data=df, x='x', y='y',
                hue='category',      # Color by category
                size='importance',    # Size by continuous variable
                style='type')         # Marker style by type
```

### 5. Control Statistical Estimation

Many functions compute statistics automatically. Understand and customize:

```python
# Lineplot computes mean and 95% CI by default
sns.lineplot(data=df, x='time', y='value',
             errorbar='sd')  # Use standard deviation instead

# Barplot computes mean by default
sns.barplot(data=df, x='category', y='value',
            estimator='median',  # Use median instead
            errorbar=('ci', 95))  # Bootstrapped CI
```

### 6. Combine with Matplotlib

Seaborn integrates seamlessly with matplotlib for fine-tuning:

```python
ax = sns.scatterplot(data=df, x='x', y='y')
ax.set(xlabel='Custom X Label', ylabel='Custom Y Label',
       title='Custom Title')
ax.axhline(y=0, color='r', linestyle='--')
plt.tight_layout()
```

### 7. Save High-Quality Figures

```python
fig = sns.relplot(data=df, x='x', y='y', col='group')
fig.savefig('figure.png', dpi=300, bbox_inches='tight')
fig.savefig('figure.pdf')  # Vector format for publications
```

## Common Patterns

### Exploratory Data Analysis

```python
# Quick overview of all relationships
sns.pairplot(data=df, hue='target', corner=True)

# Distribution exploration
sns.displot(data=df, x='variable', hue='group',
            kind='kde', fill=True, col='category')

# Correlation analysis
corr = df.corr()
sns.heatmap(corr, annot=True, cmap='coolwarm', center=0)
```

### Publication-Quality Figures

```python
sns.set_theme(style='ticks', context='paper', font_scale=1.1)

g = sns.catplot(data=df, x='treatment', y='response',
                col='cell_line', kind='box', height=3, aspect=1.2)
g.set_axis_labels('Treatment Condition', 'Response (μM)')
g.set_titles('{col_name}')
sns.despine(trim=True)

g.savefig('figure.pdf', dpi=300, bbox_inches='tight')
```

### Complex Multi-Panel Figures

```python
# Using matplotlib subplots with seaborn
fig, axes = plt.subplots(2, 2, figsize=(12, 10))

sns.scatterplot(data=df, x='x1', y='y', hue='group', ax=axes[0, 0])
sns.histplot(data=df, x='x1', hue='group', ax=axes[0, 1])
sns.violinplot(data=df, x='group', y='y', ax=axes[1, 0])
sns.heatmap(df.pivot_table(values='y', index='x1', columns='x2'),
            ax=axes[1, 1], cmap='viridis')

plt.tight_layout()
```

### Time Series with Confidence Bands

```python
# Lineplot automatically aggregates and shows CI
sns.lineplot(data=timeseries, x='date', y='measurement',
             hue='sensor', style='location', errorbar='sd')

# For more control
g = sns.relplot(data=timeseries, x='date', y='measurement',
                col='location', hue='sensor', kind='line',
                height=4, aspect=1.5, errorbar=('ci', 95))
g.set_axis_labels('Date', 'Measurement (units)')
```

## Troubleshooting

### Issue: Legend Outside Plot Area

Figure-level functions place legends outside by default. To move inside:

```python
g = sns.relplot(data=df, x='x', y='y', hue='category')
g._legend.set_bbox_to_anchor((0.9, 0.5))  # Adjust position
```

### Issue: Overlapping Labels

```python
plt.xticks(rotation=45, ha='right')
plt.tight_layout()
```

### Issue: Figure Too Small

For figure-level functions:
```python
sns.relplot(data=df, x='x', y='y', height=6, aspect=1.5)
```

For axes-level functions:
```python
fig, ax = plt.subplots(figsize=(10, 6))
sns.scatterplot(data=df, x='x', y='y', ax=ax)
```

### Issue: Colors Not Distinct Enough

```python
# Use a different palette
sns.set_palette("bright")

# Or specify number of colors
palette = sns.color_palette("husl", n_colors=len(df['category'].unique()))
sns.scatterplot(data=df, x='x', y='y', hue='category', palette=palette)
```

### Issue: KDE Too Smooth or Jagged

```python
# Adjust bandwidth
sns.kdeplot(data=df, x='x', bw_adjust=0.5)  # Less smooth
sns.kdeplot(data=df, x='x', bw_adjust=2)    # More smooth
```

## Resources

This skill includes reference materials for deeper exploration:

### references/

- `function_reference.md` - Comprehensive listing of all seaborn functions with parameters and examples
- `objects_interface.md` - Detailed guide to the modern seaborn.objects API
- `examples.md` - Common use cases and code patterns for different analysis scenarios

Load reference files as needed for detailed function signatures, advanced parameters, or specific examples.

