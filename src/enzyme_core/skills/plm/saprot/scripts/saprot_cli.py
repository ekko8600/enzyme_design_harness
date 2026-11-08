#!/usr/bin/env python3
"""SaProt zero-shot mutation scoring helpers for OpenCode skills.

The script intentionally uses the Hugging Face SaProt checkpoints directly for
mutation scoring, and only requires Foldseek when preparing 3Di tokens.
"""

from __future__ import annotations

import argparse
import csv
import json
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

AA_LIST = list("ACDEFGHIKLMNPQRSTVWY")
FOLDSEEK_STRUC_VOCAB = "pynwrqhgdlvtmfsaeikc#"


def read_fasta(path: str) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    label: str | None = None
    seq_parts: list[str] = []

    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(">"):
                if label is not None:
                    records.append((label, "".join(seq_parts)))
                label = line[1:].strip() or f"seq_{len(records) + 1}"
                seq_parts = []
            else:
                seq_parts.append(line.replace(" ", ""))

    if label is not None:
        records.append((label, "".join(seq_parts)))

    if not records:
        raise ValueError(f"No FASTA records found in {path}")
    return records


def write_fasta(records: list[tuple[str, str]], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for label, seq in records:
            handle.write(f">{label}\n{seq}\n")


def ensure_out_dir(path: str) -> Path:
    out_dir = Path(path)
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def load_model(model_name: str, device: str):
    import torch
    from transformers import EsmForMaskedLM, EsmTokenizer

    tokenizer = EsmTokenizer.from_pretrained(model_name, local_files_only=True)
    model = EsmForMaskedLM.from_pretrained(model_name, local_files_only=True)
    model.eval()
    model.to(torch.device(device))
    return model, tokenizer


def default_saprot_model() -> str:
    env_override = os.environ.get("SAPROT_MODEL") or os.environ.get("SAPROT_CHECKPOINT")
    if env_override:
        path = Path(env_override).expanduser()
        checkpoint_dir = path / "checkpoint" / "SaProt_650M_AF2"
        if checkpoint_dir.exists():
            return str(checkpoint_dir)
        return str(path)
    raise RuntimeError("SAPROT_CHECKPOINT is missing; read enzyme_core/runtime/resources.json and set the configured checkpoint path before execution")


def tokenize_combined_sequence(tokenizer, seq: str, device: str):
    if len(seq) % 2 != 0:
        raise ValueError(
            "SaProt combined sequence length must be even: uppercase AA plus "
            "lowercase 3Di token or # for each residue."
        )
    token_seq = " ".join(seq[i : i + 2] for i in range(0, len(seq), 2))
    inputs = tokenizer(token_seq, return_tensors="pt")
    return {key: value.to(device) for key, value in inputs.items()}


def command_mutate(args: argparse.Namespace) -> None:
    import torch

    if args.sequence_file:
        records=read_fasta(args.sequence_file)
        if len(records)!=1:raise ValueError('ONE_COMBINED_SEQUENCE_REQUIRED')
        args.sequence=records[0][1]
    if not args.sequence or len(args.sequence)%2:raise ValueError('INVALID_COMBINED_SEQUENCE')
    if args.mutations_file:
        loaded=json.loads(Path(args.mutations_file).read_text())
        args.mutations=[row['pose_mutant'] if isinstance(row,dict) else row for row in loaded]
    if not args.mutations or len(set(args.mutations))!=len(args.mutations):raise ValueError('EMPTY_OR_DUPLICATE_MUTATIONS')
    for mutation in args.mutations:
        positions=set()
        for single in mutation.split(':'):
            original,position,new=parse_mutation(single)
            if position<1 or position>len(args.sequence)//2 or args.sequence[2*(position-1)]!=original:raise ValueError('MUTATION_WT_OR_POSITION_MISMATCH:'+single)
            if position in positions:raise ValueError('DUPLICATE_MUTATION_POSITION')
            positions.add(position)
    if args.seed<1 or not 1<=args.threads<=4:raise ValueError('INVALID_SEED_OR_THREADS')
    torch.manual_seed(args.seed);torch.set_num_threads(args.threads)
    if args.device.startswith('cuda') and not torch.cuda.is_available():raise ValueError('REQUESTED_CUDA_UNAVAILABLE')
    out_dir = ensure_out_dir(args.out_dir)
    device = choose_device(args.device)
    print(f"Using device: {device}")
    model, tokenizer = load_model(args.model or default_saprot_model(), device)
    vocab = tokenizer.get_vocab()

    rows = []
    with torch.no_grad():
        for mut_info in args.mutations:
            tokens = [args.sequence[i : i + 2] for i in range(0, len(args.sequence), 2)]
            for single in mut_info.split(":"):
                pos = parse_mutation(single)[1]
                tokens[pos - 1] = "#" + tokens[pos - 1][-1]

            masked_seq = "".join(tokens)
            inputs = tokenize_combined_sequence(tokenizer, masked_seq, device)
            log_probs = model(**inputs).logits.log_softmax(dim=-1)[0]

            score = 0.0
            for single in mut_info.split(":"):
                ori_aa, pos, mut_aa = parse_mutation(single)
                ori_ids = token_ids_for_aa(vocab, ori_aa)
                mut_ids = token_ids_for_aa(vocab, mut_aa)
                ori_log_prob = torch.logsumexp(log_probs[pos, ori_ids],dim=0)
                mut_log_prob = torch.logsumexp(log_probs[pos, mut_ids],dim=0)
                score += (mut_log_prob-ori_log_prob).item()

            rows.append({"mutation": mut_info, "score": score})

    write_csv(out_dir / "mutation_scores.csv", rows)
    (out_dir/'scoring_manifest.json').write_text(json.dumps({'model_path':args.model or default_saprot_model(),'sequence_sha256':hashlib.sha256(args.sequence.encode()).hexdigest(),'seed':args.seed,'device':device,'threads':args.threads,'mutations':args.mutations,'score_definition':'Masked AA, retained frozen 3Di; log P(mutant AA summed over 3Di tokens) minus log P(WT AA summed over 3Di tokens). Higher is model-favored, not activity.','experimental_B_factor_masked':False},indent=2)+'\n')


def command_encode(args: argparse.Namespace) -> None:
    out_dir = ensure_out_dir(args.out_dir)
    foldseek = Path(args.foldseek_bin)
    structure = Path(args.structure)
    if not foldseek.exists():
        raise FileNotFoundError(f"Foldseek not found: {foldseek}")
    if not structure.exists():
        raise FileNotFoundError(f"Structure file not found: {structure}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_out = Path(tmp) / "foldseek.tsv"
        cmd = [
            str(foldseek),
            "structureto3didescriptor",
            "-v",
            "0",
            "--threads",
            "1",
            "--chain-name-mode",
            "1",
            str(structure),
            str(tmp_out),
        ]
        subprocess.run(cmd, check=True)

        rows = []
        fasta_records = []
        wanted_chains = set(args.chains or [])
        with open(tmp_out, "r", encoding="utf-8") as handle:
            for line in handle:
                desc, aa_seq, struc_seq = line.rstrip("\n").split("\t")[:3]
                chain = infer_chain(desc, structure.name)
                if wanted_chains and chain not in wanted_chains:
                    continue
                if args.plddt_mask:
                    plddts = extract_plddt(structure, chain)
                    if len(plddts) != len(struc_seq):
                        raise ValueError(
                            f"Length mismatch for chain {chain}: "
                            f"{len(plddts)} pLDDT values, {len(struc_seq)} 3Di tokens"
                        )
                    struc_seq = mask_low_plddt(struc_seq, plddts, args.plddt_threshold)
                combined_seq = "".join(a + s.lower() for a, s in zip(aa_seq, struc_seq))
                rows.append(
                    {
                        "label": desc,
                        "chain": chain,
                        "aa_seq": aa_seq,
                        "foldseek_seq": struc_seq,
                        "combined_seq": combined_seq,
                    }
                )
                fasta_records.append((f"{structure.stem}_{chain}", combined_seq))

    write_csv(out_dir / "structure_sequences.csv", rows)
    write_fasta(fasta_records, out_dir / "structure_sequences.fasta")


def infer_chain(desc: str, structure_name: str) -> str:
    name_chain = desc.split(" ")[0]
    return name_chain.replace(structure_name, "").split("_")[-1]


def extract_plddt(structure: Path, chain_id: str) -> np.ndarray:
    import numpy as np
    from Bio.PDB import MMCIFParser, PDBParser

    parser = MMCIFParser(QUIET=True) if structure.suffix == ".cif" else PDBParser(QUIET=True)
    parsed = parser.get_structure("protein", str(structure))
    chain = parsed[0][chain_id]
    values = []
    for residue in chain:
        atom_values = [atom.get_bfactor() for atom in residue]
        if atom_values:
            values.append(float(np.mean(atom_values)))
    return np.array(values)


def mask_low_plddt(struc_seq: str, plddts: np.ndarray, threshold: float) -> str:
    import numpy as np

    tokens = np.array(list(struc_seq))
    tokens[plddts < threshold] = "#"
    return "".join(tokens.tolist())


def parse_mutation(mutation: str) -> tuple[str, int, str]:
    if len(mutation) < 3:
        raise ValueError(f"Invalid mutation: {mutation}")
    ori_aa = mutation[0]
    mut_aa = mutation[-1]
    try:
        pos = int(mutation[1:-1])
    except ValueError as exc:
        raise ValueError(f"Invalid mutation position: {mutation}") from exc
    if ori_aa not in AA_LIST or mut_aa not in AA_LIST:
        raise ValueError(f"Invalid amino acid in mutation: {mutation}")
    return ori_aa, pos, mut_aa


def token_ids_for_aa(vocab: dict[str, int], aa: str) -> list[int]:
    token_ids = []
    for struc_token in FOLDSEEK_STRUC_VOCAB:
        token = aa + struc_token
        if token in vocab:
            token_ids.append(vocab[token])
    if not token_ids:
        raise KeyError(f"No SaProt vocabulary tokens found for amino acid {aa}")
    return token_ids


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def choose_device(requested: str) -> str:
    requested = str(requested).strip().lower()
    if requested == "cuda":
        try:
            import torch
        except Exception as exc:
            raise RuntimeError("GPU execution was requested for SaProt, but PyTorch is unavailable in this environment") from exc
        if not torch.cuda.is_available():
            raise RuntimeError("GPU execution was requested for SaProt, but no CUDA device is available")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SaProt zero-shot mutation scoring CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    encode = subparsers.add_parser(
        "encode", help="Prepare AA+3Di SaProt sequence input from PDB/mmCIF"
    )
    encode.add_argument("--structure", required=True)
    encode.add_argument("--foldseek-bin", required=True)
    encode.add_argument("--chains", nargs="*")
    encode.add_argument("--plddt-mask", action="store_true")
    encode.add_argument("--plddt-threshold", type=float, default=70.0)
    encode.add_argument("--out-dir", default="./out/saprot")
    encode.set_defaults(func=command_encode)

    mutate = subparsers.add_parser("mutate", help="Score protein mutations")
    sequence_source=mutate.add_mutually_exclusive_group(required=True)
    sequence_source.add_argument("--sequence")
    sequence_source.add_argument("--sequence-file")
    mutation_source=mutate.add_mutually_exclusive_group(required=True)
    mutation_source.add_argument("--mutations", nargs="+")
    mutation_source.add_argument("--mutations-file")
    mutate.add_argument("--seed",type=int,default=1729)
    mutate.add_argument("--threads",type=int,choices=[1,2,3,4],default=4)
    mutate.add_argument("--model", help="checkpoint path; defaults to SAPROT_MODEL or SAPROT_CHECKPOINT")
    mutate.add_argument("--device", default="cuda")
    mutate.add_argument("--out-dir", default="./out/saprot")
    mutate.set_defaults(func=command_mutate)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
