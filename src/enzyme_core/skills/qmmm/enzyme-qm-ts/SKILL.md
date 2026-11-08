---
name: enzyme-qm-ts
description: Run CP2K cluster or QM/MM reaction-path and transition-state workflows
  for enzyme and small-molecule mechanisms. Use for QM barriers, transition-state
  searches, NEB or dimer refinement, proton-transfer and acid/base catalysis, charge
  redistribution, frontier orbitals, open-shell Compound-I chemistry, and mechanism-guided
  enzyme design. Treat reduced, frozen-MM, or unconverged calculations as qualitative
  evidence unless a calibrated quantitative protocol has been validated.
metadata:
  category: qmmm
  taxonomy_version: enzyme-agent/taxonomy/v1
  capability_ids:
  - science.general_operation
---

# Enzyme QM transition-state analysis

Use CP2K to prepare, execute, and interpret auditable reaction-path and
transition-state calculations. Keep mechanistic hypotheses, computational
results, and experimentally validated claims separate.

## Evidence boundary

- A stationary point is a transition state only after it has exactly one
  relevant imaginary mode and connects the intended reactant and product.
- A frozen-MM scan is a local active-site approximation. It is not a calibrated
  activation free energy and can overestimate barriers substantially.
- A constrained classical MD ensemble around a transition-state geometry is a
  conditioning approximation, not equilibrium sampling of a saddle point.
- Classical interaction energies, NAC populations, and electrostatic
  decompositions support qualitative hypotheses; they do not establish a
  chemical barrier or catalytic rate.
- Report the functional, basis, pseudopotential, cutoff, QM region,
  protonation, charge, multiplicity, boundary treatment, sampling protocol,
  and convergence checks with every result.

## Required preflight

Run:

```bash
scripts/selfcheck.sh
```

Use the reported local CPU/GPU features as the source of truth. The supported
local route uses CP2K 2026.2 plus the existing `conda agent` environment for
OpenMM, AmberTools, RDKit, and OpenBabel. Do not install or modify that
environment automatically.

For a new workstation, read `references/migration_dependencies.md`. For the
local CP2K build, CUDA path, and runtime-library boundary, read
`references/deployment.md`.

## Select the reaction route

1. For a concerted pericyclic reaction, avoid a single bond-distance scan.
   Prefer an antisymmetric combined coordinate or start dimer refinement from
   a chemically justified TS-like geometry.
2. For proton transfer, a single bond event, or acid/base catalysis, a
   restrained coordinate can be appropriate if proton orientation and the
   nucleophile geometry are controlled.
3. For P450 Compound-I, read
   `references/cp2k_open_shell_compound_i.md` before production work. `UKS`
   plus `MULTIPLICITY` alone does not guarantee the intended broken-symmetry
   doublet or quartet state.
4. For hydrolysis or water-mediated chemistry, read
   `references/acidbase_water_nucleophile_playbook.md` before preparing the
   system.
5. For concerted Claisen chemistry, use the calibrated lessons in
   `references/chorismate_mutase_calibration.md`.

## Four-stage workflow

### 1. Prepare the system

Use `scripts/param_substrate.sh`, `scripts/place_ligand.py`, AmberTools/tleap,
and `scripts/build_qm_region.py` to construct the solvated complex. Review the
following manually before any expensive run:

- atom mapping and reaction identity;
- substrate parameters and covalent topology;
- protonation and tautomer states;
- total QM charge and spin multiplicity;
- QM-region completeness;
- link atoms and boundary placement;
- reactant and product bond definitions.

See `references/qmmm_template.md` for the common CP2K force-evaluation layout.

### 2. Optimize endpoints

Optimize reactant and product endpoints independently. Reject endpoints that
change the intended chemistry, lose essential contacts, or converge to an
incompatible electronic state.

### 3. Locate and verify the transition state

Use a restrained scan or CI-NEB to obtain a path, take a justified high-energy
image as the initial guess, refine with the dimer method, then run vibrational
analysis. Follow the CP2K motion blocks in
`references/ts_workflow_motion.md`.

For resumable restrained scans, use `scripts/run_scan.sh`; summarize the energy
profile with `scripts/analyze_scan.py`.

### 4. Analyze and report

At consistent reactant, TS, and product geometries, run single points with the
required print sections and then use:

- `scripts/analyze_charges.py` for Mulliken/Hirshfeld group charge changes;
- `scripts/analyze_ts_electrostatics.py` for heuristic residue-field ranking;
- `scripts/make_report.py` for the evidence-bounded mechanism report;
- `&MO_CUBES` and `&E_DENSITY_CUBE` for orbital or density visualization.

CP2K may reorder QM atoms by `QM_KIND`; verify the output atom map before
building group definitions. Link atoms follow the real QM atoms.

## Run CP2K

Use the auto wrapper. It prefers the CUDA build and degrades to the CPU build
when GPU acceleration is unavailable, printing the variant and the reason so the
choice is auditable:

```bash
scripts/run_cp2k_auto.sh -i input.inp -o out.out -d WORKDIR -g 0 -t 8
# resolve the variant only (readiness check), no calculation:
scripts/run_cp2k_auto.sh --dry-run
```

Pin a variant only when the run must be reproducible on a specific one. An
explicit GPU request never silently becomes a CPU run: it fails with exit code 4
when CUDA is unavailable.

```bash
CP2K_VARIANT=gpu scripts/run_cp2k_auto.sh -i input.inp -o out.out -d WORKDIR -g 0 -t 8
CP2K_VARIANT=cpu scripts/run_cp2k_auto.sh -i input.inp -o out.out -d WORKDIR -n 1 -t 2
```

`scripts/run_cp2k_gpu.sh` and `scripts/run_cp2k.sh` remain the variant-specific
wrappers behind the dispatcher; call them directly only to force one variant.
Allocate one MPI rank per visible GPU and never assume the GPU count. The local
runtime must load the toolchain libraries through `cp2k_local_env.sh`. Both
builds are declared in `the resource configuration selected by ENZYME_CORE_RESOURCES (default: runtime/resources.json)` `runtime_binaries` (`cp2k_gpu`
preferred, `cp2k_cpu` fallback) and neither uses a container image.

## Conformation and generative-model conditioning

Read `references/conformation_ensemble_workflow.md` for the full state mapping.
The supported utilities are:

- `scripts/extract_conformations.py`: export all-atom and active-site PDBs,
  geometry constraints, and contact graphs for R/NAC/TS/P states;
- `scripts/theozyme_denovo.py`: export transferable internal coordinates,
  motif PDBs, and Rosetta enzyme-design constraints;
- `scripts/run_md_ensemble.sh`: sample restrained or unrestrained state
  ensembles with OpenMM;
- `scripts/cluster_ensemble.sh`: cluster trajectories and calculate NAC
  populations for the reactant ensemble;
- `scripts/visualize_ensembles.py` and `scripts/visualize_report.py`: create
  structural figures and the self-contained HTML report.

Do not reuse a reactant topology for a product whose covalent bonds have
changed. Reparameterize product fragments explicitly. Treat the inline attack
angle and attack distance as conditioning descriptors, not proof of catalysis.

## Completion criteria

Call the work complete only when the requested scope has:

- preserved inputs, commands, logs, restarts, and software versions;
- converged endpoints and an interpretable reaction path;
- a validated TS or an explicit statement that no TS was validated;
- electronic-state checks for open-shell systems;
- a report that labels quantitative, qualitative, reduced-model, and failed
  evidence separately;
- no activity, rate, or barrier claim beyond the validated computational
  protocol.

## Resources

- `references/deployment.md`: local CP2K deployment and GPU boundary.
- `references/migration_dependencies.md`: migration checklist.
- `references/qmmm_template.md`: system and force-evaluation template.
- `references/ts_workflow_motion.md`: scan, NEB, dimer, and vibration blocks.
- `references/cp2k_open_shell_compound_i.md`: broken-symmetry Compound-I route.
- `references/chorismate_mutase_calibration.md`: concerted-reaction calibration.
- `references/acidbase_water_nucleophile_playbook.md`: hydrolysis preparation.
- `references/conformation_ensemble_workflow.md`: R/NAC/TS/P ensemble export.
