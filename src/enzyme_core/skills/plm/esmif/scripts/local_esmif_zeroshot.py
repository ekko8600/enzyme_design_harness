#!/usr/bin/env python3
"""Score protein mutants with ESM-IF1 structure-conditioned log likelihoods."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys
_CORE_ROOT=next(parent for parent in Path(__file__).resolve().parents if (parent/"skills/registry.py").is_file())
if str(_CORE_ROOT.parent) not in sys.path:sys.path.insert(0,str(_CORE_ROOT.parent))

AA_LIST = set("ACDEFGHIKLMNPQRSTVWY")


def load_esmif(esm, checkpoint=None):
    """Load legacy Namespace metadata without unrestricted pickle."""
    import torch
    from unittest.mock import patch
    safe_globals = getattr(torch.serialization, 'safe_globals', None)
    if safe_globals is None:
        raise RuntimeError('ESMIF_SAFE_WEIGHTS_ONLY_LOAD_UNAVAILABLE')
    original = torch.load; original_hub = torch.hub.load_state_dict_from_url
    def weights_only(*args, **kwargs):
        kwargs['weights_only'] = True
        return original(*args, **kwargs)
    def weights_only_hub(*args, **kwargs):
        kwargs['weights_only'] = True
        return original_hub(*args, **kwargs)
    with safe_globals([argparse.Namespace]), patch.object(torch, 'load', weights_only), patch.object(torch.hub,'load_state_dict_from_url',weights_only_hub):
        if checkpoint is not None:return esm.pretrained.load_model_and_alphabet_local(str(checkpoint))
        return esm.pretrained.esm_if1_gvp4_t16_142M_UR50()


def parse_mutation(mutation: str) -> tuple[str, int, str]:
    if len(mutation) < 3:
        raise ValueError(f"Invalid mutation: {mutation}")
    wt = mutation[0].upper()
    mt = mutation[-1].upper()
    try:
        pos = int(mutation[1:-1])
    except ValueError as exc:
        raise ValueError(f"Invalid mutation position: {mutation}") from exc
    if wt not in AA_LIST or mt not in AA_LIST:
        raise ValueError(f"Invalid amino acid in mutation: {mutation}")
    if pos < 1:
        raise ValueError(f"Mutation positions are 1-indexed: {mutation}")
    return wt, pos, mt


def apply_mutations(sequence: str, mutation_group: str) -> tuple[str, str]:
    chars = list(sequence)
    positions = []
    seen_positions = set()
    for single in mutation_group.split(":"):
        wt, pos, mt = parse_mutation(single)
        if pos > len(sequence):
            raise ValueError(f"Position {pos} exceeds sequence length {len(sequence)}")
        observed = chars[pos - 1]
        if observed != wt:
            raise ValueError(
                f"{mutation_group}: wild-type residue mismatch at {pos}; "
                f"expected {wt}, observed {observed}"
            )
        if pos in seen_positions:
            raise ValueError(f"{mutation_group}: duplicate mutation position {pos}")
        chars[pos - 1] = mt
        positions.append(str(pos))
        seen_positions.add(pos)
    return "".join(chars), ":".join(positions)


def choose_device(requested: str) -> str:
    requested = str(requested).strip().lower()
    if requested == "cuda":
        try:
            import torch
        except Exception as exc:
            raise RuntimeError("GPU execution was requested for ESM-IF, but PyTorch is unavailable in this environment") from exc
        if not torch.cuda.is_available():
            raise RuntimeError("GPU execution was requested for ESM-IF, but no CUDA device is available")
        return "cuda"
    if requested == "cpu":
        return "cpu"
    if requested != "auto":
        return requested
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def score_sequence(model, alphabet, coords, sequence: str) -> float:
    from esm.inverse_folding import util

    result = util.score_sequence(model, alphabet, coords, sequence)
    if isinstance(result, tuple):
        return float(result[0])
    return float(result)


def command_score(args: argparse.Namespace) -> None:
    import esm

    from esm.inverse_folding import util

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    coords, native_seq = util.load_coords(args.structure, args.chain)
    wildtype = (args.wildtype_sequence or native_seq).strip().upper()
    invalid = sorted(set(wildtype) - AA_LIST)
    if invalid:
        raise ValueError(f"Wild-type sequence contains unsupported amino acids: {''.join(invalid)}")
    if len(wildtype) != len(native_seq):
        raise ValueError(
            f"Wild-type sequence length {len(wildtype)} does not match parsed structure "
            f"sequence length {len(native_seq)} for chain {args.chain}"
        )

    if getattr(args,"offline",False):
        from enzyme_core.support.offline_models import model_file
        model,alphabet=load_esmif(esm,model_file("esmif","esm_if1_gvp4_t16_142M_UR50"))
    else:
        model, alphabet = load_esmif(esm)
    device = choose_device(args.device)
    print(f"Using device: {device}")
    model = model.eval().to(device)

    wildtype_ll = score_sequence(model, alphabet, coords, wildtype)

    rows = []
    for mutation_group in args.mutations:
        mutant_seq, positions = apply_mutations(wildtype, mutation_group)
        mutant_ll = score_sequence(model, alphabet, coords, mutant_seq)
        rows.append(
            {
                "mutation": mutation_group,
                "wildtype_log_likelihood": wildtype_ll,
                "mutant_log_likelihood": mutant_ll,
                "delta_log_likelihood": mutant_ll - wildtype_ll,
                "positions": positions,
            }
        )

    output = out_dir / "mutation_scores.csv"
    with open(output, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "mutation",
                "wildtype_log_likelihood",
                "mutant_log_likelihood",
                "delta_log_likelihood",
                "positions",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"Output saved to: {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score protein mutants with ESM-IF1 conditional log likelihoods"
    )
    parser.add_argument("--structure", required=True, help="PDB or mmCIF structure")
    parser.add_argument("--chain", required=True, help="Chain ID to score")
    parser.add_argument("--wildtype-sequence", help="Override sequence parsed from structure")
    parser.add_argument("--mutations", nargs="+", required=True, help="Mutations like A10G A10G:B12D")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--offline",action="store_true",help="Load configured existing local weights only")
    parser.add_argument("--out-dir", default="./out/esmif_zeroshot")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    command_score(args)


if __name__ == "__main__":
    main()
