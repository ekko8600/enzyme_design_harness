# Tool selection

Use one primary implementation for each method family. Check the local runtime
with `external_tools.py doctor` before selecting PLIP or CAVER.

| Need | Selected implementation | Do not additionally install |
| --- | --- | --- |
| Reaction paths, TS, proton transfer | `enzyme-qm-ts` (CP2K QM/MM) | Gaussian, standalone ORCA workflow, EVB, Theozyme |
| Metal/cofactor parameterization | AmberTools `MCPB.py` | separate metal-design stack |
| Mutation energy and interface ranking | existing PyRosetta workflows | FoldX, RosettaMetal, PoPMuSiC, FireProt, DynaMut2 |
| Ligand pose and contacts | PyRosetta ligand tools plus isolated PLIP | AutoDock Vina, DiffDock, Multiwfn |
| Tunnel geometry | isolated CAVER 3.0.2 | MOLE, CaverDock |
| Dynamics and water/solvent hypotheses | OpenMM MD | WaterMap, 3D-RISM, constant-pH MD stack |
| Evolution, aggregation, interfaces | existing MSA, rational workflow, and PyRosetta | ConSurf, FastML, Aggrescan3D, CamSol, PDBePISA, ASD |

Excluded tools are either redundant with the selected project route, commercial
or credential-bound (for example Gaussian and WaterMap), web-only, or would add
an unvalidated independent scoring model. Mention them only as alternatives;
never present them as installed capabilities.
