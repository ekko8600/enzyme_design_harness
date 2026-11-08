"""Small PyRosetta command-line utilities for protein design skills."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import multiprocessing as mp
import re
import time
from pathlib import Path
from typing import Any


def require_pyrosetta():
    try:
        import pyrosetta
    except ImportError as exc:
        raise SystemExit(
            "PyRosetta is not installed in this environment. Install it with "
            "`pip install pyrosetta-installer` then run a Python snippet that calls "
            "`pyrosetta_installer.install_pyrosetta()`, or use a licensed "
            "RosettaCommons PyRosetta wheel."
        ) from exc
    return pyrosetta


def init_pyrosetta(extra_options: str = ""):
    pyrosetta = require_pyrosetta()
    options = "-mute all " + extra_options
    pyrosetta.init(options.strip())
    return pyrosetta


def seeded_options(options, seed):
    """Apply the named seed in both CLI and direct registered entrypoints."""
    import shlex
    if seed is None:
        return options
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 1:
        raise ValueError('seed must be positive')
    tokens = shlex.split(options)
    clean = []; index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == '-jran': index += 2; continue
        if token == '-constant_seed': index += 1; continue
        clean.append(token); index += 1
    return shlex.join(clean + ['-constant_seed', '-jran', str(seed)])


def make_scorefxn(pyrosetta, name: str):
    if name in {"ref2015", "beta_nov16", "talaris2014"}:
        return pyrosetta.create_score_function(name)
    return pyrosetta.rosetta.core.scoring.ScoreFunctionFactory.create_score_function(name)


def command_score(args: argparse.Namespace) -> None:
    pyrosetta_options = args.options
    pyrosetta = init_pyrosetta(pyrosetta_options)
    pose = pyrosetta.pose_from_pdb(args.pdb)
    scorefxn = make_scorefxn(pyrosetta, args.scorefxn)
    total = float(scorefxn(pose))
    rows = [{"pdb": args.pdb, "scorefxn": args.scorefxn, "total_score": total}]
    write_tsv(args.out, rows, ["pdb", "scorefxn", "total_score"])


def command_clean_pdb(args: argparse.Namespace) -> None:
    pyrosetta = init_pyrosetta(args.options)
    pose = pyrosetta.pose_from_pdb(args.pdb)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pose.dump_pdb(str(out))
    write_manifest(args.manifest, {"command": "clean-pdb", "input": args.pdb, "output": str(out)})


def command_relax(args: argparse.Namespace) -> None:
    pyrosetta = init_pyrosetta(args.options)
    scorefxn = make_scorefxn(pyrosetta, args.scorefxn)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for n in range(1, args.nstruct + 1):
        pose = pyrosetta.pose_from_pdb(args.pdb)
        relax = pyrosetta.rosetta.protocols.relax.FastRelax()
        relax.set_scorefxn(scorefxn)
        relax.apply(pose)
        score = float(scorefxn(pose))
        out_pdb = out_dir / f"{Path(args.pdb).stem}_relaxed_{n:03d}.pdb"
        pose.dump_pdb(str(out_pdb))
        rows.append({"decoy": n, "pdb": str(out_pdb), "total_score": score})
    write_tsv(out_dir / "relax_scores.tsv", rows, ["decoy", "pdb", "total_score"])


def _apply_local_relax(pyrosetta, pose, scorefxn, relax_rounds: int, center_position=None, pack_radius=8.0, protection=None):
    """Apply the same chi-only relaxation used by mutation-ddg-scan."""
    movemap = pyrosetta.rosetta.core.kinematics.MoveMap()
    movemap.set_bb(False)
    movemap.set_chi(center_position is None)
    if center_position is not None:
        selection = pyrosetta.rosetta.core.select.residue_selector
        neighborhood = selection.NeighborhoodResidueSelector(selection.ResidueIndexSelector(str(center_position)), float(pack_radius), True)
        subset = neighborhood.apply(pose)
        for index in range(1, pose.total_residue() + 1):
            if subset[index]: movemap.set_chi(index, True)
    mover = pyrosetta.rosetta.protocols.relax.FastRelax()
    if protection:
        operations=pyrosetta.rosetta.core.pack.task.operation
        factory=pyrosetta.rosetta.core.pack.task.TaskFactory()
        factory.push_back(operations.RestrictToRepacking())
        locked=protection['no_repacking']|protection['no_movement']
        if locked:
            selector=pyrosetta.rosetta.core.select.residue_selector.ResidueIndexSelector(','.join(map(str,sorted(locked))))
            factory.push_back(operations.OperateOnResidueSubset(operations.PreventRepackingRLT(),selector))
        mover.set_task_factory(factory)
        for index in protection['no_movement']:movemap.set_chi(index,False)
    mover.set_scorefxn(scorefxn)
    mover.set_movemap(movemap)
    mover.set_movemap_disables_packing_of_fixed_chi_positions(True)
    if relax_rounds > 0:
        mover.max_iter(int(relax_rounds))
    mover.apply(pose)
    return pose


def _prepare_local_pose(pyrosetta, base_pose, position: int, amino_acid: str,
                        pack_radius: float, scorefxn, relax_rounds: int, protection=None):
    from pyrosetta.toolbox import mutate_residue

    pose = base_pose.clone()
    if protection:
        if position in protection['no_mutation'] and amino_acid!=base_pose.residue(position).name1():raise ValueError('PROTECTED_RESIDUE_MUTATION')
        if position in protection['no_repacking']|protection['no_movement'] and amino_acid!=base_pose.residue(position).name1():raise ValueError('PROTECTED_RESIDUE_REPACKING')
        from pyrosetta.toolbox.mutants import restrict_non_nbrs_from_repacking
        from pyrosetta.rosetta.core.chemical import aa_from_oneletter_code
        task=pyrosetta.standard_packer_task(pose)
        aa=int(aa_from_oneletter_code(amino_acid))
        task.nonconst_residue_task(position).restrict_absent_canonical_aas(pyrosetta.Vector1([i==aa for i in range(1,21)]))
        task=restrict_non_nbrs_from_repacking(pose,position,task,float(pack_radius))
        for index in protection['no_repacking']|protection['no_movement']:task.nonconst_residue_task(index).prevent_repacking()
        pyrosetta.rosetta.protocols.minimization_packing.PackRotamersMover(scorefxn,task).apply(pose)
    else:mutate_residue(pose, position, amino_acid, pack_radius=float(pack_radius))
    pose=_apply_local_relax(pyrosetta, pose, scorefxn, relax_rounds, position, pack_radius, protection)
    if protection:
        for index in protection['no_mutation']:
            if pose.residue(index).name()!=base_pose.residue(index).name():raise ValueError('FIXED_IDENTITY_CHANGED')
        for index in protection['no_movement']:
            original=base_pose.residue(index);actual=pose.residue(index)
            if original.natoms()!=actual.natoms():raise ValueError('FIXED_ATOMS_CHANGED')
            for atom in range(1,original.natoms()+1):
                if original.xyz(atom).distance(actual.xyz(atom))>1e-8:raise ValueError('FIXED_COORDINATE_CHANGED')
    return pose


def declared_protection(path,pose):
    if not path:return None
    raw=json.loads(Path(path).read_text());result={}
    for kind in ['no_mutation','no_repacking','no_movement']:
        if not isinstance(raw.get(kind),list):raise ValueError('CONSTRAINT_SEMANTICS_REQUIRED:'+kind)
        result[kind]=set()
        for item in raw[kind]:
            chain=item['chain'];insertion=item.get('insertion_code','') or ' '
            if len(chain)!=1 or len(insertion)!=1:raise ValueError('CONSTRAINT_NUMBERING_INVALID')
            index=pose.pdb_info().pdb2pose(chain,int(item['residue_number']),insertion)
            if not index:raise ValueError('CONSTRAINT_RESIDUE_NOT_FOUND')
            result[kind].add(index)
    return result


def _sha256_file(path: str | Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def command_interface(args: argparse.Namespace) -> None:
    options = seeded_options(args.options, getattr(args, 'seed', None))
    pyrosetta = init_pyrosetta(options)
    pose = pyrosetta.pose_from_pdb(args.pdb)
    mover = pyrosetta.rosetta.protocols.analysis.InterfaceAnalyzerMover(args.partners)
    mover.set_pack_separated(True)
    mover.set_compute_interface_sc(True)
    mover.apply(pose)
    rows = [
        {
            "pdb": args.pdb,
            "partners": args.partners,
            "interface_dG": float(mover.get_interface_dG()),
            "dSASA": float(mover.get_interface_delta_sasa()),
            "shape_complementarity": float(mover.get_all_data().sc_value),
            "seed": getattr(args, 'seed', None),
            "initialization_options": options,
            "pack_separated": True,
        }
    ]
    write_tsv(args.out, rows, ["pdb", "partners", "interface_dG", "dSASA", "shape_complementarity", "seed", "initialization_options", "pack_separated"])


_SCORE_TERM_NAMES = (
    "fa_atr", "fa_rep", "fa_sol", "fa_intra_rep", "fa_elec", "pro_close",
    "hbond_sr_bb", "hbond_lr_bb", "hbond_bb_sc", "hbond_sc", "dslf_fa13",
    "rama", "omega", "fa_dun", "p_aa_pp", "yhh_planarity", "ref",
    "cart_bonded", "lk_ball_wtd", "fa_intra_atr_xover4",
    "fa_intra_rep_xover4", "fa_intra_sol_xover4",
)


def _score_term_rows(pyrosetta, pose, scorefxn, *, pdb: str, mutation: str,
                     seed: int | None, decoy: int, construct: str) -> list[dict[str, Any]]:
    scorefxn(pose)
    energies = pose.energies().total_energies()
    rows: list[dict[str, Any]] = []
    for term_name in _SCORE_TERM_NAMES:
        try:
            score_type = pyrosetta.rosetta.core.scoring.score_type_from_name(term_name)
            raw = float(energies[score_type])
            weight = float(scorefxn.weights()[score_type])
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if abs(raw) < 1e-12 and abs(weight) < 1e-12:
            continue
        rows.append({
            "mutation": mutation,
            "seed": seed,
            "decoy": decoy,
            "construct": construct,
            "pdb": pdb,
            "term": term_name,
            "raw_energy": raw,
            "weight": weight,
            "weighted_energy": raw * weight,
        })
    return rows


def command_score_terms_panel(args: argparse.Namespace) -> None:
    """Score every WT/variant structure in a relaxed panel by score term."""
    panel_dir = Path(args.panel_dir)
    panel_path = panel_dir / "relaxed_panel.tsv"
    input_manifest: dict[str, Any] = {}
    try:
        input_manifest = json.loads(
            (panel_dir / "relaxed_panel_manifest.json").read_text(encoding="utf-8"))
        if not isinstance(input_manifest, dict):
            input_manifest = {}
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        input_manifest = {}
    rows: list[dict[str, Any]] = []
    with panel_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    pyrosetta = init_pyrosetta(seeded_options(args.options, getattr(args, "seed", None)))
    scorefxn = make_scorefxn(pyrosetta, args.scorefxn)
    term_rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for row in rows:
        if str(row.get("status", "")).lower() != "ok":
            continue
        mutation = str(row.get("mutation") or "")
        seed = int(row["seed"]) if row.get("seed") not in (None, "") else None
        decoy = int(row["decoy"])
        for construct, field in (("CONTROL", "control_pdb"), ("VARIANT", "variant_pdb")):
            pdb_path = Path(str(row.get(field) or ""))
            if not pdb_path.is_absolute():
                pdb_path = panel_dir / pdb_path
            try:
                pose = pyrosetta.pose_from_pdb(str(pdb_path))
                term_rows.extend(_score_term_rows(
                    pyrosetta, pose, scorefxn, pdb=pdb_path.name,
                    mutation=mutation, seed=seed, decoy=decoy, construct=construct))
            except Exception as exc:  # PyRosetta raises C++ exception wrappers.
                errors.append(f"{mutation}:{construct}:{exc}")
    fields = ["mutation", "seed", "decoy", "construct", "pdb", "term",
              "raw_energy", "weight", "weighted_energy"]
    out = Path(args.out) if args.out else panel_dir / "score_terms.tsv"
    write_tsv(out, term_rows, fields)
    write_manifest(
        args.manifest or panel_dir / "score_terms_manifest.json",
        {
            "schema_version": "enzyme-agent/pyrosetta-score-terms/v1",
            "command": "score-terms-panel",
            "panel_dir": str(panel_dir),
            "scorefxn": args.scorefxn,
            "random_seed": getattr(args, "seed", None),
            "initialization_options": seeded_options(args.options, getattr(args, "seed", None)),
            "input_pdb_sha256": input_manifest.get("input_pdb_sha256"),
            "relaxed_panel_manifest": "relaxed_panel_manifest.json",
            "input_rows": len(rows),
            "output_rows": len(term_rows),
            "terms": sorted({row["term"] for row in term_rows}),
            "errors": errors,
            "partial": bool(errors),
            "claim_ceiling": "Rosetta weighted score-term decomposition only; not thermodynamic or causal evidence.",
        },
    )


def command_mutate_scan(args: argparse.Namespace) -> None:
    pyrosetta = init_pyrosetta(args.options)
    scorefxn = make_scorefxn(pyrosetta, args.scorefxn)
    base_pose = pyrosetta.pose_from_pdb(args.pdb)
    wt_score = float(scorefxn(base_pose))
    aa_list = list(args.amino_acids)
    rows = []
    for position in parse_positions(args.positions):
        for aa in aa_list:
            pose = pyrosetta.pose_from_pdb(args.pdb)
            mutate_residue(pose, position, aa)
            if args.relax:
                relax = pyrosetta.rosetta.protocols.relax.FastRelax()
                relax.set_scorefxn(scorefxn)
                relax.apply(pose)
            mut_score = float(scorefxn(pose))
            rows.append(
                {
                    "pdb": args.pdb,
                    "pose_position": position,
                    "mutation": aa,
                    "wt_score": wt_score,
                    "mut_score": mut_score,
                    "delta_score": mut_score - wt_score,
                }
            )
    write_tsv(args.out, rows, ["pdb", "pose_position", "mutation", "wt_score", "mut_score", "delta_score"])


_WORKER: dict[str, Any] = {}


def _local_reference_score(pyrosetta, pose, position: int, wt_aa: str, pack_radius: float, scorefxn, relax: bool, relax_rounds: int) -> float:
    from pyrosetta.toolbox import mutate_residue

    baseline = pose.clone()
    mutate_residue(baseline, position, wt_aa, pack_radius=float(pack_radius))
    if relax:
        _apply_local_relax(pyrosetta, baseline, scorefxn, relax_rounds, position, pack_radius)
    return float(scorefxn(baseline))


def _ddg_worker_init(
    pdb: str,
    scorefxn_name: str,
    options: str,
    pack_radius: float,
    relax: bool,
    relax_rounds: int,
    baseline_mode: str,
) -> None:
    pyrosetta = init_pyrosetta(options)
    scorefxn = make_scorefxn(pyrosetta, scorefxn_name)
    pose = pyrosetta.pose_from_pdb(pdb)
    _WORKER.update(
        {
            "pyrosetta": pyrosetta,
            "scorefxn": scorefxn,
            "base_pose": pose,
            "wt_score": float(scorefxn(pose)),
            "pdb": pdb,
            "pack_radius": pack_radius,
            "relax": relax,
            "relax_rounds": relax_rounds,
            "baseline_mode": baseline_mode,
            "wt_local_scores": {},
        }
    )


def _ddg_worker_task(task: tuple[int, str]) -> dict[str, Any]:
    position, mutant_aa = task
    pyrosetta = _WORKER["pyrosetta"]
    from pyrosetta.toolbox import mutate_residue

    pose = _WORKER["base_pose"].clone()
    wt_aa = pose.sequence()[position - 1]
    if wt_aa == mutant_aa:
        return {
            "pdb": _WORKER["pdb"],
            "pose_position": position,
            "wt_aa": wt_aa,
            "mutant_aa": mutant_aa,
            "mutation": f"{wt_aa}{position}{mutant_aa}",
            "wt_score": _WORKER["wt_score"],
            "mut_score": _WORKER["wt_score"],
            "delta_score": 0.0,
            "status": "wt_identity",
            "error": "",
        }
    try:
        if _WORKER["baseline_mode"] == "local-repack":
            wt_local_score = _WORKER["wt_local_scores"].get(position)
            if wt_local_score is None:
                wt_local_score = _local_reference_score(
                    pyrosetta,
                    _WORKER["base_pose"],
                    position,
                    wt_aa,
                    float(_WORKER["pack_radius"]),
                    _WORKER["scorefxn"],
                    bool(_WORKER["relax"]),
                    int(_WORKER["relax_rounds"]),
                )
                _WORKER["wt_local_scores"][position] = wt_local_score
        else:
            wt_local_score = float(_WORKER["wt_score"])
        mutate_residue(pose, position, mutant_aa, pack_radius=float(_WORKER["pack_radius"]))
        if _WORKER["relax"]:
            _apply_local_relax(pyrosetta, pose, _WORKER["scorefxn"],
                               int(_WORKER["relax_rounds"]), position, float(_WORKER["pack_radius"]))
        mut_score = float(_WORKER["scorefxn"](pose))
        return {
            "pdb": _WORKER["pdb"],
            "pose_position": position,
            "wt_aa": wt_aa,
            "mutant_aa": mutant_aa,
            "mutation": f"{wt_aa}{position}{mutant_aa}",
            "wt_score": _WORKER["wt_score"],
            "wt_score_local": wt_local_score,
            "mut_score": mut_score,
            "delta_score": mut_score - float(wt_local_score),
            "delta_score_global": mut_score - float(_WORKER["wt_score"]),
            "status": "ok",
            "error": "",
        }
    except Exception as exc:  # PyRosetta raises several C++ exception wrappers.
        return {
            "pdb": _WORKER["pdb"],
            "pose_position": position,
            "wt_aa": wt_aa,
            "mutant_aa": mutant_aa,
            "mutation": f"{wt_aa}{position}{mutant_aa}",
            "wt_score": _WORKER["wt_score"],
            "wt_score_local": "",
            "mut_score": "",
            "delta_score": "",
            "delta_score_global": "",
            "status": "failed",
            "error": str(exc),
        }


def append_tsv_row(path: str | Path, row: dict[str, Any], fieldnames: list[str]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    exists = out.exists()
    with out.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def parse_mutation_list(path: str | None) -> list[tuple[int, str]]:
    if not path:
        return []
    rows: list[tuple[int, str]] = []
    with Path(path).open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return rows
        for row in reader:
            mutation = (row.get("mutation") or row.get("mutant") or "").strip().upper()
            if len(mutation) >= 3 and mutation[1:-1].isdigit():
                rows.append((int(mutation[1:-1]), mutation[-1]))
                continue
            pos = row.get("pose_position") or row.get("position") or row.get("residue")
            aa = row.get("mutant_aa") or row.get("mutation") or row.get("aa")
            if pos and str(pos).isdigit() and aa:
                rows.append((int(pos), str(aa).strip().upper()[0]))
    return rows


def command_mutation_ddg_scan(args: argparse.Namespace) -> None:
    tasks = parse_mutation_list(args.mutation_csv)
    if not tasks:
        if not args.positions:
            raise SystemExit("Provide --positions or --mutation-csv")
        aa_list = list(args.amino_acids)
        tasks = [(position, aa) for position in parse_positions(args.positions) for aa in aa_list]
    if args.max_tasks is not None:
        tasks = tasks[: max(0, args.max_tasks)]
    if args.skip_wt:
        pyrosetta = init_pyrosetta(args.options)
        pose = pyrosetta.pose_from_pdb(args.pdb)
        tasks = [(pos, aa) for pos, aa in tasks if pose.sequence()[pos - 1] != aa]

    out = Path(args.out)
    fieldnames = [
        "pdb",
        "pose_position",
        "wt_aa",
        "mutant_aa",
        "mutation",
        "wt_score",
        "wt_score_local",
        "mut_score",
        "delta_score",
        "delta_score_global",
        "status",
        "error",
    ]
    existing: dict[str, dict[str, Any]] = {}
    if args.resume and out.exists():
        with out.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            for row in reader:
                key = row.get("mutation") or f"{row.get('pose_position')}:{row.get('mutant_aa')}"
                existing[key] = row
    if existing:
        completed = {
            (int(row["pose_position"]), row["mutant_aa"])
            for row in existing.values()
            if row.get("status") == "ok" and str(row.get("pose_position", "")).isdigit() and row.get("mutant_aa")
        }
        tasks = [task for task in tasks if task not in completed]

    if not tasks and existing:
        rows = list(existing.values())
    else:
        rows = list(existing.values())
        started_at = time.monotonic()
        partial = False
        if not args.resume and out.exists():
            out.unlink()
        if args.resume and out.exists():
            pass
        elif rows:
            write_tsv(out, rows, fieldnames)
        def timed_out() -> bool:
            return args.max_runtime_seconds is not None and (time.monotonic() - started_at) >= args.max_runtime_seconds

        if args.workers <= 1:
            _ddg_worker_init(
                args.pdb,
                args.scorefxn,
                args.options,
                args.pack_radius,
                args.relax,
                args.relax_rounds,
                args.baseline_mode,
            )
            for task in tasks:
                if timed_out():
                    partial = True
                    break
                row = _ddg_worker_task(task)
                rows.append(row)
                append_tsv_row(out, row, fieldnames)
        else:
            ctx = mp.get_context("spawn")
            pool = ctx.Pool(
                processes=args.workers,
                initializer=_ddg_worker_init,
                initargs=(
                    args.pdb,
                    args.scorefxn,
                    args.options,
                    args.pack_radius,
                    args.relax,
                    args.relax_rounds,
                    args.baseline_mode,
                ),
            )
            try:
                for row in pool.imap_unordered(_ddg_worker_task, tasks, chunksize=max(1, args.chunk_size)):
                    if timed_out():
                        partial = True
                        pool.terminate()
                        break
                    rows.append(row)
                    append_tsv_row(out, row, fieldnames)
                if not partial:
                    pool.close()
            finally:
                pool.join()
    if existing:
        by_mutation = {row["mutation"]: row for row in existing.values()}
        by_mutation.update({row["mutation"]: row for row in rows})
        rows = sorted(by_mutation.values(), key=lambda row: (int(row["pose_position"]), row["mutant_aa"]))
    else:
        rows = sorted(rows, key=lambda row: (int(row["pose_position"]), row["mutant_aa"]))
    numbering_map = None
    if args.numbering_map:
        try:
            payload = json.loads(Path(args.numbering_map).read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not payload:
                raise ValueError
            numbering_map = {int(key): int(value) for key, value in payload.items()}
            if any(key < 1 or value < 1 for key, value in numbering_map.items()):
                raise ValueError
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise SystemExit("--numbering-map must be a non-empty JSON object of pose to full positions") from error
        for row in rows:
            position = int(row["pose_position"])
            if position not in numbering_map:
                raise SystemExit(f"--numbering-map is missing pose position {position}")
            full_position = numbering_map[position]
            row["full_sequence_position"] = full_position
            row["full_mutation"] = f"{row['wt_aa']}{full_position}{row['mutant_aa']}"
    output_fields = fieldnames + (["full_sequence_position", "full_mutation"] if numbering_map else [])
    write_tsv(out, rows, output_fields)
    write_manifest(
        args.manifest,
        {
            "command": "mutation-ddg-scan",
            "pdb": args.pdb,
            "scorefxn": args.scorefxn,
            "workers": args.workers,
            "pack_radius": args.pack_radius,
            "relax": args.relax,
            "relax_rounds": args.relax_rounds,
            "baseline_mode": args.baseline_mode,
            "options": args.options,
            "random_seed": (
                int(seed_match.group(1))
                if (seed_match := re.search(r"(?:^|\s)-jran\s+(\d+)(?:\s|$)", args.options))
                else None
            ),
            "output": str(out),
            "attempted_mutations": len(rows),
            "remaining_tasks": max(0, len(tasks) - max(0, len(rows) - len(existing))),
            "partial": partial if "partial" in locals() else False,
            "numbering_map": numbering_map,
            "note": "PyRosetta/Rosetta scoring is CPU-bound; use process-level parallelism rather than GPU acceleration.",
        },
    )


def command_mutation_relax_panel(args: argparse.Namespace) -> None:
    """Write matched WT/variant relaxed structures for downstream analysis.

    This intentionally mirrors mutation-ddg-scan's local-repack baseline.  It
    is a structure-producing companion, not a second research planner or an
    evidence admission path.
    """
    tasks = parse_mutation_list(args.mutation_csv)
    if not tasks:
        if not args.positions:
            raise SystemExit("Provide --positions or --mutation-csv")
        tasks = [(position, aa) for position in parse_positions(args.positions)
                 for aa in list(args.amino_acids)]
    if args.max_tasks is not None:
        tasks = tasks[: max(0, args.max_tasks)]
    if not tasks:
        raise SystemExit("No mutations were provided")
    if args.nstruct < 1:
        raise SystemExit("--nstruct must be at least 1")
    if args.relax_rounds < 1:
        raise SystemExit("--relax-rounds must be at least 1")

    pyrosetta_options = args.options
    pyrosetta = init_pyrosetta(pyrosetta_options)
    scorefxn = make_scorefxn(pyrosetta, args.scorefxn)
    base_pose = pyrosetta.pose_from_pdb(args.pdb)
    out_dir = Path(args.out_dir)
    protection=declared_protection(getattr(args,'constraints_file',None),base_pose)
    out_dir.mkdir(parents=True, exist_ok=True)
    seed_match = re.search(r"(?:^|\s)-jran\s+(\d+)(?:\s|$)", pyrosetta_options)
    random_seed = int(seed_match.group(1)) if seed_match else None
    seed_label = str(random_seed) if random_seed is not None else "default"
    rows: list[dict[str, Any]] = []

    for position, mutant_aa in tasks:
        if position < 1 or position > base_pose.total_residue():
            rows.append({
                "mutation": f"?{position}{mutant_aa}", "pose_position": position,
                "wt_aa": "", "mutant_aa": mutant_aa, "seed": random_seed,
                "decoy": None, "control_pdb": "", "variant_pdb": "",
                "control_score": "", "variant_score": "", "delta_score": "",
                "status": "failed", "error": "pose position out of range",
            })
            continue
        wt_aa = base_pose.sequence()[position - 1]
        mutation = f"{wt_aa}{position}{mutant_aa}"
        for decoy in range(1, args.nstruct + 1):
            try:
                control_pose = _prepare_local_pose(
                    pyrosetta, base_pose, position, wt_aa, args.pack_radius,
                    scorefxn, args.relax_rounds, protection)
                variant_pose = _prepare_local_pose(
                    pyrosetta, base_pose, position, mutant_aa, args.pack_radius,
                    scorefxn, args.relax_rounds, protection)
                control_path = out_dir / f"control_{mutation}_seed_{seed_label}_decoy_{decoy:03d}.pdb"
                variant_path = out_dir / f"variant_{mutation}_seed_{seed_label}_decoy_{decoy:03d}.pdb"
                control_pose.dump_pdb(str(control_path))
                variant_pose.dump_pdb(str(variant_path))
                control_score = float(scorefxn(control_pose))
                variant_score = float(scorefxn(variant_pose))
                exported_control_score = float(scorefxn(pyrosetta.pose_from_pdb(str(control_path))))
                exported_variant_score = float(scorefxn(pyrosetta.pose_from_pdb(str(variant_path))))
                rows.append({
                    "mutation": mutation, "pose_position": position,
                    "wt_aa": wt_aa, "mutant_aa": mutant_aa,
                    "seed": random_seed, "decoy": decoy,
                    "control_pdb": control_path.name, "variant_pdb": variant_path.name,
                    "control_score": control_score, "variant_score": variant_score,
                    "delta_score": variant_score - control_score,
                    "exported_control_score": exported_control_score,
                    "exported_variant_score": exported_variant_score,
                    "exported_delta_score": exported_variant_score - exported_control_score,
                    "status": "ok", "error": "",
                })
            except Exception as exc:  # PyRosetta raises C++ exception wrappers.
                rows.append({
                    "mutation": mutation, "pose_position": position,
                    "wt_aa": wt_aa, "mutant_aa": mutant_aa,
                    "seed": random_seed, "decoy": decoy,
                    "control_pdb": "", "variant_pdb": "",
                    "control_score": "", "variant_score": "", "delta_score": "",
                    "status": "failed", "error": str(exc),
                })

    fields = [
        "mutation", "pose_position", "wt_aa", "mutant_aa", "seed", "decoy",
        "control_pdb", "variant_pdb", "control_score", "variant_score",
        "delta_score", "status", "error",
        "exported_control_score", "exported_variant_score", "exported_delta_score",
    ]
    write_tsv(out_dir / "relaxed_panel.tsv", rows, fields)
    write_manifest(
        args.manifest or out_dir / "relaxed_panel_manifest.json",
        {
            "schema_version": "enzyme-agent/pyrosetta-relaxed-panel/v1",
            "command": "mutation-relax-panel",
            "constraints_file_sha256":_sha256_file(args.constraints_file) if args.constraints_file else None,
            "constraints_pose_positions":{k:sorted(v) for k,v in protection.items()} if protection else None,
            "pdb": args.pdb,
            "input_pdb_sha256": _sha256_file(args.pdb),
            "scorefxn": args.scorefxn,
            "pack_radius": args.pack_radius,
            "relax": True,
            "relax_rounds": args.relax_rounds,
            "baseline_mode": "local-repack",
            "options": pyrosetta_options,
            "random_seed": random_seed,
            "nstruct": args.nstruct,
            "attempted_rows": len(rows),
            "successful_rows": sum(1 for row in rows if row["status"] == "ok"),
            "partial": any(row["status"] != "ok" for row in rows),
            "mutation_panel": [row["mutation"] for row in rows if row["status"] == "ok"],
            "claim_ceiling": "Matched relaxed structural proxy only; not thermodynamic stability, activity or mechanism validation.",
            "note": "WT controls are position-matched local-repack/chi-only FastRelax structures.",
            "score_coordinate_states": {"control_score": "raw in-memory Pose before PDB precision loss", "variant_score": "raw in-memory Pose before PDB precision loss", "exported_control_score": "saved PDB reloaded and scored", "exported_variant_score": "saved PDB reloaded and scored", "exported_delta_score": "exported variant minus exported control, REU"},
        },
    )


def command_smoke_test(args: argparse.Namespace) -> None:
    pyrosetta = init_pyrosetta(args.options)
    from pyrosetta.toolbox import mutate_residue

    pose = pyrosetta.pose_from_sequence("ACDEFGHIKLMNPQRSTVWY")
    scorefxn = make_scorefxn(pyrosetta, args.scorefxn)
    initial_score = float(scorefxn(pose))
    mutate_residue(pose, 5, "A")
    mutated_score = float(scorefxn(pose))
    if args.relax:
        relax = pyrosetta.rosetta.protocols.relax.FastRelax()
        relax.set_scorefxn(scorefxn)
        relax.max_iter(1)
        relax.apply(pose)
    print(
        json.dumps(
            {
                "python": f"{__import__('sys').version_info.major}.{__import__('sys').version_info.minor}",
                "pyrosetta_file": getattr(pyrosetta, "__file__", "unknown"),
                "sequence": pose.sequence(),
                "initial_score": initial_score,
                "mutated_score": mutated_score,
                "relax": args.relax,
            },
            indent=2,
        )
    )


def command_pack_design(args: argparse.Namespace) -> None:
    pyrosetta = init_pyrosetta(args.options)
    pose = pyrosetta.pose_from_pdb(args.pdb)
    scorefxn = make_scorefxn(pyrosetta, args.scorefxn)
    task_factory = pyrosetta.rosetta.core.pack.task.TaskFactory()
    task_factory.push_back(pyrosetta.rosetta.core.pack.task.operation.InitializeFromCommandline())
    task_factory.push_back(pyrosetta.rosetta.core.pack.task.operation.IncludeCurrent())
    if args.repack_only:
        task_factory.push_back(pyrosetta.rosetta.core.pack.task.operation.RestrictToRepacking())
    packer = pyrosetta.rosetta.protocols.minimization_packing.PackRotamersMover(scorefxn)
    packer.task_factory(task_factory)
    packer.apply(pose)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pose.dump_pdb(str(out))
    write_manifest(
        args.manifest,
        {
            "command": "pack-design",
            "input": args.pdb,
            "output": str(out),
            "score": float(scorefxn(pose)),
            "repack_only": args.repack_only,
        },
    )


def parse_positions(value: str) -> list[int]:
    positions = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            positions.extend(range(int(start), int(end) + 1))
        else:
            positions.append(int(part))
    return positions


def write_tsv(path: str | Path, rows: list[dict], fieldnames: list[str]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def write_manifest(path: str | None, payload: dict) -> None:
    if not path:
        return
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PyRosetta helper protocols")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--seed", type=int, default=None, help="Explicit Rosetta random seed")
    common.add_argument("--ligand-params", nargs="+", help="Reviewed Rosetta residue params files")
    sub = parser.add_subparsers(dest="command", required=True)

    score = sub.add_parser("score", help="Score a PDB", parents=[common])
    score.add_argument("--pdb", required=True)
    score.add_argument("--scorefxn", default="ref2015")
    score.add_argument("--out", default="out/pyrosetta-scoring/scores.tsv")
    score.add_argument("--options", default="")
    score.set_defaults(func=command_score)

    clean = sub.add_parser("clean-pdb", help="Load and dump a cleaned PDB through PyRosetta", parents=[common])
    clean.add_argument("--pdb", required=True)
    clean.add_argument("--out", required=True)
    clean.add_argument("--manifest", default=None)
    clean.add_argument("--options", default="")
    clean.set_defaults(func=command_clean_pdb)

    relax = sub.add_parser("relax", help="Run FastRelax", parents=[common])
    relax.add_argument("--pdb", required=True)
    relax.add_argument("--nstruct", type=int, default=1)
    relax.add_argument("--scorefxn", default="ref2015")
    relax.add_argument("--out-dir", default="out/pyrosetta-relax")
    relax.add_argument("--options", default="")
    relax.set_defaults(func=command_relax)

    interface = sub.add_parser("interface", help="Compute interface metrics", parents=[common])
    interface.add_argument("--pdb", required=True)
    interface.add_argument("--partners", required=True, help="Rosetta partners string, e.g. A_B")
    interface.add_argument("--out", default="out/pyrosetta-interface/interface.tsv")
    interface.add_argument("--options", default="")
    interface.set_defaults(func=command_interface)

    score_terms = sub.add_parser(
        "score-terms-panel", help="decompose score terms for a relaxed WT/variant panel", parents=[common])
    score_terms.add_argument("--panel-dir", required=True)
    score_terms.add_argument("--scorefxn", default="ref2015")
    score_terms.add_argument("--out")
    score_terms.add_argument("--manifest")
    score_terms.add_argument("--options", default="-constant_seed")
    score_terms.set_defaults(func=command_score_terms_panel)

    mutate = sub.add_parser("mutate-scan", help="Scan point mutations by pose position", parents=[common])
    mutate.add_argument("--pdb", required=True)
    mutate.add_argument("--positions", required=True, help="Pose positions, e.g. 35,42,88 or 35-40")
    mutate.add_argument("--amino-acids", default="ACDEFGHIKLMNPQRSTVWY")
    mutate.add_argument("--scorefxn", default="ref2015")
    mutate.add_argument("--relax", action="store_true")
    mutate.add_argument("--out", default="out/pyrosetta-design/mutations.tsv")
    mutate.add_argument("--options", default="")
    mutate.set_defaults(func=command_mutate_scan)

    ddg = sub.add_parser("mutation-ddg-scan", help="Parallel local-repack mutation ddG scan", parents=[common])
    ddg.add_argument("--pdb", required=True)
    ddg.add_argument("--positions", default="", help="Pose positions, e.g. 35,42,88 or 35-40")
    ddg.add_argument("--mutation-csv", help="Optional CSV containing mutation/mutant or position+mutant_aa columns")
    ddg.add_argument("--amino-acids", default="ACDEFGHIKLMNPQRSTVWY")
    ddg.add_argument("--scorefxn", default="ref2015")
    ddg.add_argument("--pack-radius", type=float, default=8.0)
    ddg.add_argument("--relax", action="store_true", help="Run chi-only FastRelax after mutation and local repack")
    ddg.add_argument("--relax-rounds", type=int, default=25)
    ddg.add_argument("--workers", type=int, default=1)
    ddg.add_argument("--chunk-size", type=int, default=1)
    ddg.add_argument("--baseline-mode", choices=["local-repack", "global"], default="local-repack")
    ddg.add_argument("--max-runtime-seconds", type=float, default=None)
    ddg.add_argument("--max-tasks", type=int, default=None)
    ddg.add_argument("--skip-wt", action="store_true", help="Skip identity mutations")
    ddg.add_argument("--resume", action="store_true", help="Merge with an existing TSV instead of overwriting completed rows")
    ddg.add_argument("--numbering-map", help="JSON object mapping pose positions to full-sequence positions")
    ddg.add_argument("--out", default="out/pyrosetta-design/mutation_ddg.tsv")
    ddg.add_argument("--manifest", default="out/pyrosetta-design/mutation_ddg_manifest.json")
    ddg.add_argument("--options", default="-constant_seed")
    ddg.set_defaults(func=command_mutation_ddg_scan)

    relaxed_panel = sub.add_parser(
        "mutation-relax-panel",
        help="Write matched WT/variant local-repack and relax structures", parents=[common])
    relaxed_panel.add_argument("--pdb", required=True)
    relaxed_panel.add_argument("--positions", default="",
                               help="Pose positions, e.g. 35,42,88 or 35-40")
    relaxed_panel.add_argument(
        "--mutation-csv",
        help="CSV containing mutation/mutant or position+mutant_aa columns")
    relaxed_panel.add_argument("--amino-acids", default="ACDEFGHIKLMNPQRSTVWY")
    relaxed_panel.add_argument("--scorefxn", default="ref2015")
    relaxed_panel.add_argument("--pack-radius", type=float, default=8.0)
    relaxed_panel.add_argument("--relax-rounds", type=int, default=5)
    relaxed_panel.add_argument("--nstruct", type=int, default=1)
    relaxed_panel.add_argument("--constraints-file", help="Reviewed no_mutation/no_repacking/no_movement residue declarations")
    relaxed_panel.add_argument("--max-tasks", type=int, default=None)
    relaxed_panel.add_argument("--out-dir", required=True)
    relaxed_panel.add_argument("--manifest", default=None)
    relaxed_panel.add_argument("--options", default="-constant_seed")
    relaxed_panel.set_defaults(func=command_mutation_relax_panel)

    pack = sub.add_parser("pack-design", help="Run packer/design mover", parents=[common])
    pack.add_argument("--pdb", required=True)
    pack.add_argument("--out", default="out/pyrosetta-design/packed.pdb")
    pack.add_argument("--manifest", default="out/pyrosetta-design/manifest.json")
    pack.add_argument("--scorefxn", default="ref2015")
    pack.add_argument("--repack-only", action="store_true")
    pack.add_argument("--options", default="")
    pack.set_defaults(func=command_pack_design)

    smoke = sub.add_parser("smoke-test", help="Check PyRosetta import, scoring, mutation, and optional relax", parents=[common])
    smoke.add_argument("--scorefxn", default="ref2015")
    smoke.add_argument("--relax", action="store_true")
    smoke.add_argument("--options", default="")
    smoke.set_defaults(func=command_smoke_test)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.seed is not None:
        if args.seed < 1:
            parser.error("seed must be positive")
        args.options = seeded_options(args.options, args.seed)
    if args.ligand_params:
        import shlex
        for path in args.ligand_params:
            if not Path(path).is_file():
                parser.error("ligand params missing")
        args.options += " -extra_res_fa " + " ".join(shlex.quote(str(Path(p).resolve())) for p in args.ligand_params)
    args.func(args)


if __name__ == "__main__":
    main()
