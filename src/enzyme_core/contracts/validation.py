"""Validate explicitly requested output artifacts, not scientific conclusions."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from .models import ArtifactSpec


def validate_artifacts(work_dir: Path, specifications: tuple[ArtifactSpec, ...], *, forbidden_values=()) -> dict:
    root = Path(work_dir).resolve()
    results = []
    for spec in specifications:
        path = (root / spec.path).resolve()
        row = {"path": spec.path, "format": spec.format, "valid": False}
        try:
            if root not in path.parents:
                raise ValueError("ARTIFACT_ESCAPES_WORK_DIRECTORY")
            if spec.format=='collection':
                from enzyme_core.runtime.collections import manifest,tree_hash
                members=manifest(path)
                if forbidden_values:
                    checks=validate_artifacts(path,tuple(ArtifactSpec(m['path']) for m in members),forbidden_values=forbidden_values)
                    if checks['status']!='VALID':raise ValueError('CREDENTIAL_VALUE_IN_ARTIFACT_COLLECTION')
                if not members:raise ValueError('COLLECTION_EMPTY')
                row.update(valid=True,sha256=tree_hash(path),size_bytes=sum(m['size_bytes'] for m in members),members=members,validation_level='COLLECTION_INTEGRITY')
                results.append(row);continue
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError("ARTIFACT_MISSING_OR_EMPTY")
            raw = path.read_bytes() if spec.format != "file" else None
            if spec.format == "json":
                data = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("JSON_NONFINITE:" + value)))
                for key in spec.required_keys:
                    current = data
                    for part in key.split("."):
                        if not isinstance(current, dict) or part not in current:
                            raise ValueError("JSON_KEY_MISSING:" + key)
                        current = current[part]
            elif spec.format in {"csv","tsv"}:
                with path.open(newline="", encoding="utf-8") as handle:
                    reader = csv.DictReader(handle, delimiter="\t" if spec.format=="tsv" else ",")
                    if not set(spec.required_columns).issubset(reader.fieldnames or ()):
                        raise ValueError("CSV_COLUMNS_MISSING")
                    if not list(reader):
                        raise ValueError("CSV_DATA_EMPTY")
            elif spec.format == "fasta":
                lines = [line.strip() for line in raw.decode().splitlines() if line.strip()]
                sequence = False
                for index, line in enumerate(lines):
                    if line.startswith(">"):
                        if len(line) < 2 or (index > 0 and sequence is False):
                            raise ValueError("FASTA_RECORD_INVALID")
                        sequence = False
                    elif not lines[0].startswith(">") or not line.replace("-", "").replace("*", "").isalpha():
                        raise ValueError("FASTA_SEQUENCE_INVALID")
                    else:
                        sequence = True
                if not sequence:
                    raise ValueError("FASTA_SEQUENCE_MISSING")
            elif spec.format == "pdb":
                atoms = [line for line in raw.decode().splitlines() if line.startswith(("ATOM  ", "HETATM"))]
                if not atoms:
                    raise ValueError("PDB_ATOMS_MISSING")
                import math
                for line in atoms:
                    if not all(math.isfinite(float(line[a:b])) for a, b in ((30, 38), (38, 46), (46, 54))):
                        raise ValueError("PDB_COORDINATES_NONFINITE")
            elif spec.format == 'image':
                from .formats import image
                image(raw)
            elif spec.format == 'cif':
                from .formats import cif
                cif(raw)
            elif spec.format == "html":
                text = raw.decode().lower()
                if "<html" not in text or "</html>" not in text:
                    raise ValueError("HTML_DOCUMENT_INCOMPLETE")
            if forbidden_values:
                tail=b'';overlap=max(map(len,forbidden_values))-1
                with path.open('rb') as stream:
                    for chunk in iter(lambda:stream.read(1024*1024),b''):
                        combined=tail+chunk
                        if any(value in combined for value in forbidden_values):raise ValueError('CREDENTIAL_VALUE_IN_ARTIFACT')
                        tail=combined[-overlap:] if overlap else b''
            digest = hashlib.sha256()
            if raw is None:
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
            else:
                digest.update(raw)
            row.update(valid=True, sha256=digest.hexdigest(), size_bytes=path.stat().st_size, validation_level="EXISTENCE_AND_HASH" if spec.format=="file" else "STRUCTURE")
        except (OSError, ValueError, UnicodeError, csv.Error) as exc:
            row["error"] = str(exc)
        results.append(row)
    return {"status": "NOT_REQUESTED" if not results else "VALID" if all(r["valid"] for r in results) else "INVALID",
            "artifacts": results, "scientific_validation": "NOT_ASSESSED"}
