---
name: pdb
description: 'Fetch and analyze protein structures from RCSB PDB. Use this skill when:
  (1) Need to download a structure by PDB ID, (2) Search for similar structures, (3)
  Prepare target for binder design, (4) Extract specific chains or domains, (5) Get
  structure metadata.

  For sequence lookup, use uniprot. For binder design workflow, use binder-design.

  '
metadata:
  category: data
  tags: database, structure, fetch
  legacy_category: utilities
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - data.scientific_database.retrieval
---

# PDB Database Access

**Quick alias**: the canonical implementation is `pdb-database`. Use its
wrapper scripts for every RCSB access — they enforce rate limits, license
notification, and schema-aware queries. Do **not** hand-roll `curl` or
`requests` calls against RCSB APIs; that bypasses the documented wrapper and is
a discipline violation under AGENTS.md.

## Fetching a Structure

```bash
# PDB format
enzyme-core run pdb-database --work-dir ./runs/pdb-database --operation download_coordinate_files -- \
  --ids "1ALU" --format "pdb" --output_dir structures/

# mmCIF format
enzyme-core run pdb-database --work-dir ./runs/pdb-database --operation download_coordinate_files -- \
  --ids "1ALU" --format "mmcif" --output_dir structures/
```

## Searching PDB

Use `search_pdb.py` for attribute, sequence, structure, motif, chemical, and
full-text search. Fetch the schema first to discover the right attributes:

```bash
enzyme-core run pdb-database --work-dir ./runs/pdb-database --operation fetch_schema -- --api search_structure --output schema.txt
enzyme-core run pdb-database --work-dir ./runs/pdb-database --operation search_pdb -- --query '<JSON query>' --return_type entry --output results.json
```

Sequence-similarity example (no schema fetch needed):

```bash
enzyme-core run pdb-database --work-dir ./runs/pdb-database --operation search_pdb -- --query '{ "query": { "type": "terminal", "service": "sequence", "parameters": { "evalue_cutoff": 1, "identity_cutoff": 0.9, "sequence_type": "protein", "value": "MKTAYIAKQRQISFVK..." } }, "request_options": { "scoring_strategy": "sequence" } }' --return_type polymer_entity --output results.json
```

## Metadata

Prefer metadata queries over downloading full coordinate files when you only
need entry-level facts:

```bash
enzyme-core run pdb-database --work-dir ./runs/pdb-database --operation fetch_pdb_metadata -- \
  --query '{ entries(entry_ids:["1ALU"]) { rcsb_id struct { title } exptl { method } } }' --output metadata.json
```

## Local Structure Handling

Once the file is downloaded, local processing with Bio.PDB is fine (no API
calls). Common steps:

### Selecting Chains

```python
from Bio.PDB import PDBParser, PDBIO, Select

class ChainSelect(Select):
    def __init__(self, chain_id):
        self.chain_id = chain_id
    def accept_chain(self, chain):
        return chain.id == self.chain_id

parser = PDBParser(QUIET=True)
structure = parser.get_structure("protein", "structures/1ALU.pdb")
io = PDBIO()
io.set_structure(structure)
io.save("chain_A.pdb", ChainSelect("A"))
```

### Common Tasks for Binder Design

1. Download structure via `pdb-database` (see above).
2. Identify target chain.
3. Remove waters and ligands (if needed).
4. Trim to binding region + buffer.
5. Identify potential hotspots.
6. Renumber if needed.

## Troubleshooting

**Structure not found**: Check PDB ID format (4 characters).
**Multiple models**: Select first model for design.
**Missing residues**: Check for gaps in structure.
**Wrapper missing/error**: Read `pdb-database/SKILL.md`; do not fall back to
raw HTTP without recording the wrapper failure first.

---

**Next**: Use structure with `boltz` (recommended) for structure validation, or
`pyrosetta-pose` for modeling.
