"""Explicit KIC and local docking protocols. No reference coordinates are accepted.

These operations perform searches, not score-only substitutes. Scientific recovery
is evaluated separately against evaluator-owned references.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path
from xml.sax.saxutils import escape


def checksum(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def method_execution(structures, requested):
    complete=len(structures)==requested and all(r['algorithm_move_succeeded'] for r in structures)
    return {'status':'COMPLETED' if complete else 'FAILED','requested_decoys':requested,
        'successful_decoys':sum(r['algorithm_move_succeeded'] for r in structures),
        'failed_decoys':[r['path'] for r in structures if not r['algorithm_move_succeeded']],
        'scope':'Algorithm execution only; structural recovery quality is evaluated separately.'}


def parameter_provenance(paths):
    """Bind each explicitly loaded residue-parameter file to its exact bytes."""
    return [
        {"path": str(Path(path).resolve()), "sha256": checksum(path)}
        for path in (paths or [])
    ]


def main():
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", choices=["kic", "ligand-local", "protein-local"], required=True)
    parser.add_argument("--input-pdb", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--nstruct", type=int, default=1)
    parser.add_argument("--loops-json", help="JSON list of pose-index start/end/cut records")
    parser.add_argument("--ligand-params", nargs="+")
    parser.add_argument("--ligand-chain", default="X")
    parser.add_argument("--partners", help="Validated Rosetta chain grouping")
    parser.add_argument("--cycles", type=int, default=6)
    args = parser.parse_args()
    if args.seed < 1 or args.nstruct < 1 or args.cycles < 1:
        parser.error("seed/nstruct/cycles must be positive")
    if args.protocol == "kic" and not args.loops_json:
        parser.error("KIC requires reviewed loops JSON")
    if args.protocol == "ligand-local" and not args.ligand_params:
        parser.error("Ligand docking requires explicit residue params")
    if args.protocol == "protein-local" and not args.partners:
        parser.error("Protein docking requires explicit partners")
    if len(args.ligand_chain) != 1 or not args.ligand_chain.isalnum():
        parser.error("ligand chain must be one alphanumeric character")
    if args.partners and args.protocol != "protein-local":
        parser.error("partners is only applicable to protein-local")
    if args.partners:
        import re
        if not re.fullmatch(r"[A-Za-z0-9]+_[A-Za-z0-9]+", args.partners):
            parser.error("invalid partners")
        a, b = args.partners.split("_")
        if set(a) & set(b):
            parser.error("partner groups overlap")
    import pyrosetta
    from pyrosetta.rosetta import protocols
    import shlex
    options = f"-mute all -constant_seed -jran {args.seed}"
    if args.ligand_params:
        if not all(Path(p).is_file() for p in args.ligand_params):
            parser.error("missing ligand params")
        options += " -extra_res_fa " + " ".join(shlex.quote(str(Path(p).resolve())) for p in args.ligand_params)
    pyrosetta.init(options)
    original = pyrosetta.pose_from_pdb(args.input_pdb)
    out = Path(args.out_dir)
    if out.exists() and any(out.iterdir()):
        raise ValueError("OUTPUT_DIRECTORY_NOT_EMPTY")
    out.mkdir(parents=True, exist_ok=True)
    ranking_scorefxn='ligand' if args.protocol=='ligand-local' else 'ref2015'
    score = pyrosetta.create_score_function(ranking_scorefxn)
    manifest = {"protocol": args.protocol, "seed": args.seed, "nstruct": args.nstruct,
                "input_sha256": checksum(args.input_pdb), "search_executed": True,
                "reference_coordinates_available_to_protocol": False,
                "ranking_scorefxn":ranking_scorefxn,"ranking_score_field":"exported_pdb_score_REU",
                "cycles_parameter_applies_to":"ligand-local only",
                "parameters": vars(args),
                "loaded_ligand_params": parameter_provenance(args.ligand_params),
                "structures": [],
                "boundary": "Local search execution and score are not proof of native recovery, binding free energy or enzyme activity."}
    for i in range(args.nstruct):
        decoy_started, cpu_started = time.monotonic(), time.process_time()
        pose = original.clone()
        search_details = {}
        if args.protocol == "kic":
            loops = protocols.loops.Loops()
            definitions = json.loads(Path(args.loops_json).read_text())
            if not definitions:
                raise ValueError("EMPTY_LOOP_DEFINITION")
            if len(definitions) != 1:
                raise ValueError("KIC_REQUIRES_ONE_LOOP_PER_SEARCH")
            start, end, cut = (int(definitions[0][k]) for k in ["start", "end", "cut"])
            precondition_loop = protocols.loops.Loop(start, end, cut)
            protocols.loops.set_single_loop_fold_tree(pose, precondition_loop)
            protocols.loops.idealize_loop(pose, precondition_loop)
            manifest["kic_preconditioning"] = "set_single_loop_fold_tree+idealize_loop on declared search bounds before LoopModeler KIC"
            for loop in definitions:
                start, end, cut = (int(loop[k]) for k in ["start", "end", "cut"])
                if not 1 < start <= cut < end < pose.total_residue():
                    raise ValueError("INVALID_LOOP_RANGE")
                chains = {pose.chain(j) for j in range(start - 1, end + 2)}
                if len(chains) != 1 or not all(pose.residue(j).is_protein() for j in range(start - 1, end + 2)):
                    raise ValueError("LOOP_CROSSES_CHAIN_OR_NONPROTEIN")
                loops.add_loop(protocols.loops.Loop(start, end, cut))
            mover = protocols.loop_modeler.LoopModeler()
            mover.setup_kic_config()
            mover.set_loops(loops)
            mover.apply(pose)
            method = "LoopModeler.setup_kic_config"
            move_success=mover.get_last_move_status()==protocols.moves.MS_SUCCESS
        elif args.protocol == "protein-local":
            chains = {pose.pdb_info().chain(j) for j in range(1, pose.total_residue() + 1)}
            if not set(args.partners.replace("_", "")) <= chains:
                raise ValueError("PARTNER_CHAIN_NOT_FOUND")
            xml = f'''<ROSETTASCRIPTS><SCOREFXNS><ScoreFunction name="dock" weights="ref2015"/></SCOREFXNS>
            <MOVERS><DockingProtocol name="search" partners="{escape(args.partners)}"
            docking_local_refine="1" docking_score_high="dock"/></MOVERS>
            <PROTOCOLS><Add mover_name="search"/></PROTOCOLS></ROSETTASCRIPTS>'''
            mover = protocols.rosetta_scripts.XmlObjects.create_from_string(xml).get_mover("search")
            movable_jumps=pyrosetta.rosetta.utility.vector1_int();movable_jumps.append(1)
            protocols.docking.setup_foldtree(pose,args.partners,movable_jumps,False)
            mover.set_movable_jumps(movable_jumps)
            manifest['docking_fold_tree']=str(pose.fold_tree())
            mover.apply(pose)
            method = "RosettaDock.high_resolution_local_search"
            highres_status = mover.docking_highres_mover().get_last_move_status()
            outer_status = mover.get_last_move_status()
            # The native wrapper may reject a fully executed search at its score
            # filter. Preserve that rejection; it is not a missing algorithm run.
            move_success = highres_status == protocols.moves.MS_SUCCESS
            search_details = {'high_resolution_move_status':str(highres_status),
                'native_protocol_move_status':str(outer_status),
                'native_protocol_accepted':outer_status == protocols.moves.MS_SUCCESS,
                'native_filters_modified':False}
        else:
            if not any(pose.pdb_info().chain(j) == args.ligand_chain and not pose.residue(j).is_protein()
                       for j in range(1, pose.total_residue() + 1)):
                raise ValueError("LIGAND_CHAIN_NOT_FOUND")
            xml = f'''<ROSETTASCRIPTS><SCOREFXNS>
            <ScoreFunction name="soft" weights="ligand_soft_rep"/>
            <ScoreFunction name="hard" weights="ligand"/></SCOREFXNS>
            <LIGAND_AREAS><LigandArea name="side" chain="{args.ligand_chain}" cutoff="6.0"
            add_nbr_radius="true" all_atom_mode="true"/></LIGAND_AREAS>
            <INTERFACE_BUILDERS><InterfaceBuilder name="interface" ligand_areas="side"/></INTERFACE_BUILDERS>
            <MOVEMAP_BUILDERS><MoveMapBuilder name="map" sc_interface="interface" minimize_water="false"/></MOVEMAP_BUILDERS>
            <MOVERS><HighResDocker name="search" cycles="{args.cycles}" repack_every_Nth="3"
            scorefxn="soft" movemap_builder="map"/>
            <FinalMinimizer name="finish" scorefxn="hard" movemap_builder="map"/></MOVERS>
            <PROTOCOLS><Add mover_name="search"/><Add mover_name="finish"/></PROTOCOLS></ROSETTASCRIPTS>'''
            objects = protocols.rosetta_scripts.XmlObjects.create_from_string(xml)
            search=objects.get_mover("search");finish=objects.get_mover("finish")
            search.apply(pose);search_status=search.get_last_move_status()
            finish.apply(pose);finish_status=finish.get_last_move_status()
            move_success=search_status==protocols.moves.MS_SUCCESS and finish_status==protocols.moves.MS_SUCCESS
            method = "HighResDocker+FinalMinimizer"
        status = str(mover.get_last_move_status()) if args.protocol != "ligand-local" else f'HighResDocker:{search_status};FinalMinimizer:{finish_status}'
        path = out / f"decoy_{i + 1:03d}.pdb"
        pose.dump_pdb(str(path))
        raw_score=float(score(pose))
        exported_score=float(score(pyrosetta.pose_from_pdb(str(path))))
        manifest["structures"].append({"path": path.name, "sha256": checksum(path),
            "total_score_REU":raw_score,"raw_pose_score_REU":raw_score,
            "exported_pdb_score_REU":exported_score,"ranking_coordinate_state":"exported_reloaded_PDB",
            "method": method, "move_status": status,"algorithm_move_succeeded":bool(move_success),
            "wall_seconds": time.monotonic() - decoy_started, "cpu_seconds": time.process_time() - cpu_started,
            "random_stream": {"initial_seed": args.seed, "decoy_index": i + 1}, **search_details})
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    manifest['method_execution']=method_execution(manifest['structures'],args.nstruct)
    manifest['wall_seconds'] = time.monotonic() - started
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    if manifest['method_execution']['status']!='COMPLETED':raise SystemExit('REQUIRED_SEARCH_ALGORITHM_FAILED')


if __name__ == "__main__":
    main()
