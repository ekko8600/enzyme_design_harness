#!/usr/bin/env python3
"""Score protein mutants with ESM-2 masked-marginal zero-shot log ratios."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
_CORE_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "skills" / "registry.py").is_file())
if str(_CORE_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_CORE_ROOT.parent))

AA_LIST = set("ACDEFGHIKLMNPQRSTVWY")


def load_esm2(model_name: str, offline=False):
    import esm

    if not hasattr(esm.pretrained, model_name):
        raise ValueError(
            f"Unknown fair-esm model {model_name!r}. Example: esm2_t33_650M_UR50D"
        )
    checkpoint=None
    if offline:
        from enzyme_core.support.offline_models import model_file
        checkpoint=model_file("esm",model_name)
        model_file("esm",model_name+"-contact-regression")
    from unittest.mock import patch
    import torch
    # fair-esm checkpoints may contain Namespace metadata. Keep the allowlist
    # scoped and force safe deserialization for both local and hub loaders.
    safe_globals = getattr(torch.serialization, 'safe_globals', None)
    if safe_globals is None:raise RuntimeError('ESM_SAFE_WEIGHTS_ONLY_LOAD_UNAVAILABLE')
    original = torch.load;original_hub = torch.hub.load_state_dict_from_url
    def weights_only(*args, **kwargs):
        kwargs['weights_only'] = True
        return original(*args, **kwargs)
    def weights_only_hub(*args, **kwargs):
        kwargs['weights_only'] = True
        return original_hub(*args, **kwargs)
    with safe_globals([argparse.Namespace]), patch.object(torch, 'load', weights_only), patch.object(torch.hub,'load_state_dict_from_url',weights_only_hub):
        if offline:return esm.pretrained.load_model_and_alphabet_local(str(checkpoint))
        return getattr(esm.pretrained, model_name)()


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


def validate_mutations(sequence: str, mutation_group: str) -> list[tuple[str, int, str]]:
    parsed = [parse_mutation(item) for item in mutation_group.split(":")]
    seen_positions = set()
    for wt, pos, _ in parsed:
        if pos > len(sequence):
            raise ValueError(f"Position {pos} exceeds sequence length {len(sequence)}")
        observed = sequence[pos - 1]
        if observed != wt:
            raise ValueError(
                f"{mutation_group}: wild-type residue mismatch at {pos}; "
                f"expected {wt}, observed {observed}"
            )
        if pos in seen_positions:
            raise ValueError(f"{mutation_group}: duplicate mutation position {pos}")
        seen_positions.add(pos)
    return parsed


def masked_sequence(sequence: str, mutations: list[tuple[str, int, str]]) -> str:
    chars = list(sequence)
    for _, pos, _ in mutations:
        chars[pos - 1] = "<mask>"
    return "".join(chars)


def choose_device(requested: str) -> str:
    requested = str(requested).strip().lower()
    if requested == "cuda":
        try:
            import torch
        except Exception as exc:
            raise RuntimeError("GPU execution was requested for ESM2, but PyTorch is unavailable in this environment") from exc
        if not torch.cuda.is_available():
            raise RuntimeError("GPU execution was requested for ESM2, but no CUDA device is available")
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


def command_score(args: argparse.Namespace) -> None:
    import torch

    if args.batch_size < 1:
        raise ValueError("batch-size must be positive")
    from enzyme_core.support.sequences import protein_sequence
    sequence = protein_sequence(sequence=args.sequence, fasta=getattr(args, "sequence_fasta", None))
    invalid = sorted(set(sequence) - AA_LIST)
    if invalid:
        raise ValueError(f"Sequence contains unsupported amino acids: {''.join(invalid)}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    model, alphabet = load_esm2(args.model,offline=getattr(args,"offline",False))
    batch_converter = alphabet.get_batch_converter()
    device = choose_device(args.device)
    print(f"Using device: {device}")
    model.eval().to(device)

    mutation_groups = list(args.mutations or ())
    if args.mutation_file:
        mutation_groups.extend(
            line.strip() for line in Path(args.mutation_file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    if not mutation_groups:
        raise ValueError("At least one mutation or --mutation-file is required")

    parsed_inputs = []
    for mutation_group in mutation_groups:
        mutations = validate_mutations(sequence, mutation_group)
        parsed_inputs.append((mutation_group, mutations, masked_sequence(sequence, mutations)))

    rows = []
    with torch.no_grad():
        for start in range(0, len(parsed_inputs), args.batch_size):
            batch = parsed_inputs[start:start + args.batch_size]
            data = [(mutation_group, masked) for mutation_group, _mutations, masked in batch]
            _, _, tokens = batch_converter(data)
            tokens = tokens.to(device)
            log_probs = model(tokens)["logits"].log_softmax(dim=-1)
            for index, (mutation_group, mutations, _masked) in enumerate(batch):
                score = 0.0
                positions = []
                for wt, pos, mt in mutations:
                    token_pos = pos
                    wt_idx = alphabet.get_idx(wt)
                    mt_idx = alphabet.get_idx(mt)
                    score += (log_probs[index, token_pos, mt_idx]
                              - log_probs[index, token_pos, wt_idx]).item()
                    positions.append(str(pos))
                rows.append(
                    {
                        "mutation": mutation_group,
                        # CPU and CUDA kernels can differ in the last few
                        # decimals. Three decimals is finer than the resolution
                        # warranted by this descriptive ranking and makes the
                        # collected artifact stable across the declared devices.
                        "score": round(score, 3),
                        "positions": ":".join(positions),
                    }
                )

    output = out_dir / "mutation_scores.csv"
    with open(output, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["mutation", "score", "positions"])
        writer.writeheader()
        writer.writerows(rows)
    # Keep a machine-readable mirror for the ResearchPlane's deterministic
    # JSON-pointer ObservationExtractor.  CSV remains the convenient human
    # artifact; this JSON is emitted from the same in-memory rows so the two
    # artifacts cannot diverge during a Host collection step.
    json_output = out_dir / "mutation_scores.json"
    json_output.write_text(
        json.dumps(rows, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(f"Output saved to: {output}")
    print(f"JSON output saved to: {json_output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score protein mutants with ESM-2 masked-marginal log ratios"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sequence", help="Wild-type protein sequence literal")
    group.add_argument("--sequence-fasta", help="Exactly one protein FASTA record")
    parser.add_argument("--mutations", nargs="*", help="Mutations like A10G A10G:B12D")
    parser.add_argument("--mutation-file", help="UTF-8 file containing one mutation group per line")
    parser.add_argument("--model", default="esm2_t33_650M_UR50D")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--offline", action="store_true", help="Use configured local checkpoint and regression weights; never download")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--out-dir", default="./out/esm_zeroshot")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    command_score(args)


if __name__ == "__main__":
    main()
