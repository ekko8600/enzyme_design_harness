---
name: boltz
description: 'Structure prediction using the locally registered Boltz-2 installation.
  Use this skill when: (1) Predicting protein complex structures, (2) Validating designed
  binders, (3) Need open-source alternative to AF2, (4) Predicting protein-ligand
  complexes, (5) Using local GPU resources.

  For QC thresholds, use protein-qc. For AlphaFold2 prediction, use alphafold. For
  Chai prediction, use chai.

  '
metadata:
  category: structure
  tags: structure-prediction, validation, open-source
  local_script: scripts/local_boltz.py
  legacy_category: design-tools
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - structure.analysis
---

# Boltz Structure Prediction

## Prerequisites

Before running, inspect `the resource configuration selected by ENZYME_CORE_RESOURCES (default: runtime/resources.json)` and use its Boltz environment
and local-weight binding as the source of truth. Do not download weights or
silently substitute a different model installation.

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| Python | 3.10+ | 3.11 |
| CUDA | 12.0+ | 12.1+ |
| GPU VRAM | 24GB | 48GB (L40S) |
| RAM | 32GB | 64GB |

## How to run

> **First time?** Install the local model package for this tool and use the bundled local wrapper in the `scripts/` directory. No cloud account is required.

### Direct local execution

Create a Boltz YAML in the task directory and validate it before using a GPU.
The wrapper writes `run_manifest.json` beside the prediction output.

For a protein-ligand complex, use this exact schema; do not inspect the
installed Boltz package or call the vendor CLI to rediscover it:

```yaml
sequences:
  - protein:
      id: A
      sequence: PROTEIN_SEQUENCE
  - ligand:
      id: L
      smiles: LIGAND_SMILES
```

Print the template locally if needed:

```bash
enzyme-core run boltz --work-dir ./runs/boltz --operation local_boltz -- --show-protein-ligand-template
```

```bash
enzyme-core run boltz --work-dir ./runs/boltz --operation local_boltz -- \
  --input-yaml runs/<task-id>/inputs/boltz_input.yaml \
  --out-dir runs/<task-id>/outputs \
  --run-name boltz-2 \
  --dry-run
```

After the user confirms the resource use, remove `--dry-run` and run the same
command. Use a local weight cache only; enabling a network MSA service requires
an explicit user decision.

```bash
enzyme-core run boltz --work-dir ./runs/boltz --operation local_boltz -- \
  --input-yaml runs/<task-id>/inputs/boltz_input.yaml \
  --out-dir runs/<task-id>/outputs \
  --run-name boltz-2
```

**GPU**: L40S (48GB) | **Timeout**: 1800s default

## Key parameters

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `--recycling_steps` | 3 | 1-10 | Recycling iterations |
| `--sampling_steps` | 200 | 50-500 | Diffusion steps |
| `use_msa_server` | false | bool | Requires a network-enabled adapter policy |

## FASTA Format

```
>protein_A
MKTAYIAKQRQISFVK...
>protein_B
MVLSPADKTNVKAAWG...
```

## Output format

```
predictions/
├── model_0.cif       # Best model (CIF format)
├── confidence.json   # pLDDT, pTM, ipTM
└── pae.npy          # PAE matrix
```

**Note**: Boltz outputs CIF format. Convert to PDB if needed:
```python
from Bio.PDB import MMCIFParser, PDBIO
parser = MMCIFParser()
structure = parser.get_structure("model", "model_0.cif")
io = PDBIO()
io.set_structure(structure)
io.save("model_0.pdb")
```

## Comparison

| Feature | Boltz-1 | Boltz-2 | AF2-Multimer |
|---------|---------|---------|--------------|
| MSA-free mode | Yes | Yes | No |
| Diffusion | Yes | Yes | No |
| Speed | Fast | Faster | Slower |
| Open source | Yes | Yes | Yes |

## Sample output

### Successful run
```
$ boltz predict --fasta complex.fasta --output predictions/
[INFO] Loading Boltz-1 weights...
[INFO] Predicting structure...
[INFO] Saved model to predictions/model_0.cif

predictions/confidence.json:
{
  "ptm": 0.78,
  "iptm": 0.65,
  "plddt": 0.81
}
```

**What good output looks like:**
- pTM: > 0.7 (confident global structure)
- ipTM: > 0.5 (confident interface)
- pLDDT: > 0.7 (confident per-residue)
- CIF file: ~100-500 KB for typical complex

## Decision tree

```
Should I use Boltz?
│
├─ What are you predicting?
│  ├─ Protein-protein complex → Boltz ✓ or Chai or ColabFold
│  ├─ Protein + ligand → Boltz ✓ or Chai
│  └─ Single protein → Use ESMFold (faster)
│
├─ Need MSA?
│  ├─ No / want speed → Boltz ✓
│  └─ Yes / maximum accuracy → ColabFold
│
└─ Why Boltz over Chai?
   ├─ Open weights preference → Boltz ✓
   ├─ Boltz-2 speed → Boltz ✓
   └─ DNA/RNA support → Consider Chai
```

## Typical performance

| Campaign Size | Time (L40S) | Compute requirement | Notes |
|---------------|-------------|--------------|-------|
| 100 complexes | 30-45 min | ~$8 | Standard validation |
| 500 complexes | 2-3h | ~$35 | Large campaign |
| 1000 complexes | 4-6h | ~$70 | Comprehensive |

**Per-complex**: ~15-30s for typical binder-target complex.

---

## Verify

```bash
find predictions -name "*.cif" | wc -l  # Should match input count
```

---

## Troubleshooting

**Low confidence**: Increase recycling_steps
**OOM errors**: Use MSA-free mode or A100-80GB
**Slow prediction**: Reduce sampling_steps

### Error interpretation

| Error | Cause | Fix |
|-------|-------|-----|
| `RuntimeError: CUDA out of memory` | Complex too large | Use `--use_msa_server false` or larger GPU |
| `KeyError: 'iptm'` | Single chain only | Ensure FASTA has 2+ chains |
| `FileNotFoundError: weights` | Missing model | Run `boltz download` first |
| `ValueError: invalid residue` | Non-standard AA | Check for modified residues in sequence |

**Next**: `protein-qc` for filtering and ranking.
