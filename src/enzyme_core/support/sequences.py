"""Explicit protein sequence inputs. File paths are never interpreted as residues."""
from pathlib import Path

AMINO_ACIDS = frozenset('ACDEFGHIKLMNPQRSTVWY')


def protein_sequence(*, sequence=None, fasta=None):
    if (sequence is not None) == (fasta is not None):
        raise ValueError('SEQUENCE_INPUT_EXACTLY_ONE_REQUIRED')
    if fasta is not None:
        path = Path(fasta)
        if not path.is_file():
            raise ValueError('FASTA_FILE_MISSING:' + str(path))
        records = []
        current = None
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith('>'):
                current = []
                records.append(current)
            elif current is None:
                raise ValueError('FASTA_HEADER_REQUIRED')
            else:
                current.append(line)
        if len(records) != 1:
            raise ValueError('FASTA_SINGLE_SEQUENCE_REQUIRED')
        sequence = ''.join(records[0])
    value = str(sequence).strip().upper()
    if not value or set(value) - AMINO_ACIDS:
        raise ValueError('PROTEIN_SEQUENCE_INVALID: use --sequence-fasta for a file input')
    return value
