# Extended guide

Read this reference when the task needs the advanced procedures, examples, or troubleshooting moved out of the core Skill instructions.

## Contents

- [Common Workflows](#common-workflows)
  - [Workflow 1: Enzyme Discovery for New Substrate](#workflow-1-enzyme-discovery-for-new-substrate)
  - [Workflow 2: Cross-Organism Enzyme Comparison](#workflow-2-cross-organism-enzyme-comparison)
  - [Workflow 3: Enzyme Engineering Target Identification](#workflow-3-enzyme-engineering-target-identification)
  - [Workflow 4: Enzymatic Pathway Construction](#workflow-4-enzymatic-pathway-construction)
  - [Workflow 5: Kinetic Parameter Analysis](#workflow-5-kinetic-parameter-analysis)
  - [Workflow 6: Industrial Enzyme Selection](#workflow-6-industrial-enzyme-selection)
- [Data Formats and Parsing](#data-formats-and-parsing)
  - [BRENDA Response Format](#brenda-response-format)
  - [Data Extraction Patterns](#data-extraction-patterns)
- [Reference Documentation](#reference-documentation)
- [Troubleshooting](#troubleshooting)
- [Additional Resources](#additional-resources)

## Common Workflows

### Workflow 1: Enzyme Discovery for New Substrate

Find suitable enzymes for a specific substrate:

```python
from enzyme_core.support.brenda_client import get_km_values
from scripts.brenda_queries import search_enzymes_by_substrate, compare_substrate_affinity

# Search for enzymes that act on substrate
substrate = "2-phenylethanol"
enzymes = search_enzymes_by_substrate(substrate, limit=15)

print(f"Found {len(enzymes)} enzymes for {substrate}")
for enzyme in enzymes:
    print(f"EC {enzyme['ec_number']}: {enzyme['enzyme_name']}")

# Get kinetic data for best candidates
if enzymes:
    best_ec = enzymes[0]['ec_number']
    km_data = get_km_values(best_ec, substrate=substrate)

    if km_data:
        print(f"Kinetic data for {best_ec}:")
        for entry in km_data[:3]:  # First 3 entries
            print(f"  {entry}")
```

### Workflow 2: Cross-Organism Enzyme Comparison

Compare enzyme properties across different organisms:

```python
from scripts.brenda_queries import compare_across_organisms, get_environmental_parameters

# Define organisms for comparison
organisms = [
    "Escherichia coli",
    "Saccharomyces cerevisiae",
    "Bacillus subtilis",
    "Thermus thermophilus"
]

# Compare alcohol dehydrogenase
comparison = compare_across_organisms("1.1.1.1", organisms)

print("Cross-organism comparison:")
for org_data in comparison:
    print(f"\n{org_data['organism']}:")
    print(f"  Average Km: {org_data['average_km']}")
    print(f"  Optimal pH: {org_data['optimal_ph']}")
    print(f"  Temperature: {org_data['optimal_temperature']}°C")

# Get detailed environmental parameters
env_params = get_environmental_parameters("1.1.1.1")
print(f"\nOverall optimal pH range: {env_params['ph_range']}")
```

### Workflow 3: Enzyme Engineering Target Identification

Find engineering opportunities for enzyme improvement:

```python
from scripts.brenda_queries import (
    find_thermophilic_homologs,
    find_ph_stable_variants,
    compare_substrate_affinity
)

# Find thermophilic variants for heat stability
thermophilic = find_thermophilic_homologs("1.1.1.1", min_temp=50)
print(f"Found {len(thermophilic)} thermophilic variants")

# Find alkaline-stable variants
alkaline = find_ph_stable_variants("1.1.1.1", min_ph=8.0)
print(f"Found {len(alkaline)} alkaline-stable variants")

# Compare substrate specificities for engineering targets
specificity = compare_substrate_affinity("1.1.1.1")
print("Substrate affinity ranking:")
for i, sub in enumerate(specificity[:5]):
    print(f"  {i+1}. {sub['name']}: Km = {sub['km']}")
```

### Workflow 4: Enzymatic Pathway Construction

Build enzymatic synthesis pathways:

```python
from scripts.enzyme_pathway_builder import (
    find_pathway_for_product,
    build_retrosynthetic_tree,
    calculate_pathway_feasibility
)

# Find pathway to target product
target = "lactate"
pathway = find_pathway_for_product(target, max_steps=3)

if pathway:
    print(f"Found pathway to {target}:")
    for i, step in enumerate(pathway['steps']):
        print(f"  Step {i+1}: {step['reaction']}")
        print(f"    Enzyme: EC {step['ec_number']}")
        print(f"    Organism: {step['organism']}")

# Evaluate pathway feasibility
feasibility = calculate_pathway_feasibility(pathway)
print(f"\nPathway feasibility score: {feasibility['score']}/10")
print(f"Potential issues: {feasibility['warnings']}")
```

### Workflow 5: Kinetic Parameter Analysis

Comprehensive kinetic analysis for enzyme selection:

```python
from enzyme_core.support.brenda_client import get_km_values
from scripts.brenda_queries import parse_km_entry, get_modeling_parameters
from scripts.brenda_visualization import plot_kinetic_parameters

# Get comprehensive kinetic data
ec_number = "1.1.1.1"
km_data = get_km_values(ec_number)

# Analyze kinetic parameters
all_entries = []
for entry in km_data:
    parsed = parse_km_entry(entry)
    if parsed['km_value']:
        all_entries.append(parsed)

print(f"Analyzed {len(all_entries)} kinetic entries")

# Find best kinetic performer
best_km = min(all_entries, key=lambda x: x['km_value'])
print(f"\nBest kinetic performer:")
print(f"  Organism: {best_km['organism']}")
print(f"  Substrate: {best_km['substrate']}")
print(f"  Km: {best_km['km_value']}")

# Get modeling parameters
model_data = get_modeling_parameters(ec_number, substrate=best_km['substrate'])
print(f"\nModeling parameters:")
print(f"  Km: {model_data['km']}")
print(f"  kcat: {model_data['kcat']}")
print(f"  Vmax: {model_data['vmax']}")

# Generate visualization
plot_kinetic_parameters(ec_number)
```

### Workflow 6: Industrial Enzyme Selection

Select enzymes for industrial applications:

```python
from scripts.brenda_queries import (
    find_thermophilic_homologs,
    get_environmental_parameters,
    get_inhibitors
)

# Industrial criteria: high temperature tolerance, organic solvent resistance
target_enzyme = "1.1.1.1"

# Find thermophilic variants
thermophilic = find_thermophilic_homologs(target_enzyme, min_temp=60)
print(f"Thermophilic candidates: {len(thermophilic)}")

# Check solvent tolerance (inhibitor data)
inhibitors = get_inhibitors(target_enzyme)
solvent_tolerant = [
    inv for inv in inhibitors
    if 'ethanol' not in inv['name'].lower() and
       'methanol' not in inv['name'].lower()
]

print(f"Solvent tolerant candidates: {len(solvent_tolerant)}")

# Evaluate top candidates
for candidate in thermophilic[:3]:
    print(f"\nCandidate: {candidate['organism']}")
    print(f"  Optimal temp: {candidate['optimal_temperature']}°C")
    print(f"  Km: {candidate['km']}")
    print(f"  pH range: {candidate.get('ph_range', 'N/A')}")
```

## Data Formats and Parsing

### BRENDA Response Format

BRENDA returns data in specific formats that need parsing:

**Km Value Format**:
```
organism*Escherichia coli#substrate*ethanol#kmValue*1.2#kmValueMaximum*#commentary*pH 7.4, 25°C#ligandStructureId*#literature*
```

**Reaction Format**:
```
ecNumber*1.1.1.1#organism*Saccharomyces cerevisiae#reaction*ethanol + NAD+ <=> acetaldehyde + NADH + H+#commentary*#literature*
```

### Data Extraction Patterns

```python
import re

def parse_brenda_field(data, field_name):
    """Extract specific field from BRENDA data entry"""
    pattern = f"{field_name}\\*([^#]*)"
    match = re.search(pattern, data)
    return match.group(1) if match else None

def extract_multiple_values(data, field_name):
    """Extract multiple values for a field"""
    pattern = f"{field_name}\\*([^#]*)"
    matches = re.findall(pattern, data)
    return [match for match in matches if match.strip()]
```

## Reference Documentation

For detailed BRENDA documentation, see `references/api_reference.md`. This includes:
- Complete SOAP API method documentation
- Full parameter lists and formats
- EC number structure and validation
- Response format specifications
- Error codes and handling
- Data field definitions
- Literature citation formats

## Troubleshooting

**Authentication Errors**:
- Verify BRENDA_EMAIL and BRENDA_PASSWORD in .env file
- Check for correct spelling (note BRENDA_EMIAL legacy support)
- Ensure BRENDA account is active and has API access

**No Results Returned**:
- Try broader searches with wildcards (*)
- Check EC number format (e.g., "1.1.1.1" not "1.1.1")
- Verify substrate spelling and naming
- Some enzymes may have limited data in BRENDA

**Rate Limiting**:
- Add delays between requests (0.5-1 second)
- Cache results locally
- Use more specific queries to reduce data volume
- Consider batch operations for multiple queries

**Network Errors**:
- Check internet connection
- BRENDA server may be temporarily unavailable
- Try again after a few minutes
- Consider using VPN if geo-restricted

**Data Format Issues**:
- Use the provided parsing functions in scripts
- BRENDA data can be inconsistent in formatting
- Handle missing fields gracefully
- Validate parsed data before use

**Performance Issues**:
- Large queries can be slow; limit search scope
- Use specific organism or substrate filters
- Consider asynchronous processing for batch operations
- Monitor memory usage with large datasets

## Additional Resources

- BRENDA Home: https://www.brenda-enzymes.org/
- BRENDA SOAP API Documentation: https://www.brenda-enzymes.org/soap.php
- Enzyme Commission (EC) Numbers: https://www.qmul.ac.uk/sbcs/iubmb/enzyme/
- Zeep SOAP Client: https://python-zeep.readthedocs.io/
- Enzyme Nomenclature: https://www.iubmb.org/enzyme/
