# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""MSA computation — prefers boltz (ColabFold MMseqs2) with EBI Clustal Omega fallback.

For a SINGLE query sequence, uses boltz's built-in ColabFold MMseqs2 client
to search UniRef + environmental databases, producing a high-quality A3M.
For MULTIPLE pre-collected sequences, falls back to EBI Clustal Omega.
"""

import argparse
import os
import sys
import time
import urllib.parse
from pathlib import Path

import dotenv
import sys
from pathlib import Path as _ProjectPath
PROJECT_ROOT = next(parent for parent in _ProjectPath(__file__).resolve().parents if (parent / "skills" / "registry.py").is_file()).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from enzyme_core.support import http_client

_POLLING_TIMEOUT_SECS = 15 * 60  # 15 minutes.

_CLIENT = http_client.HttpClient(
    "https://www.ebi.ac.uk/Tools/services/rest/clustalo/", qps=1
)


def _prepare_payload(email: str, title: str, sequences: str) -> bytes:
  """Prepares the payload for the EBI Clustal Omega API."""
  params = {
      "email": email,
      "title": title,
      "sequence": sequences,
  }
  return urllib.parse.urlencode(params).encode("utf-8")


def _align_sequences(
    *, input_file: str, output_file: str, dry_run: bool = False,
    method: str = "auto",
) -> None:
  """Run MSA — tries boltz (ColabFold MMseqs2) first, falls back to EBI Clustal.

  Parameters
  ----------
  input_file : str
      Path to FASTA file with sequences.
  output_file : str
      Path for the output alignment file (A3M or FASTA).
  dry_run : bool
      Print payload and exit.
  method : str
      "auto" (default) → try boltz for single seq, else EBI.
      "boltz" → force boltz (ColabFold MMseqs2).
      "ebi" → force EBI Clustal Omega.
  """
  if not os.path.exists(input_file):
    print(f"[!] Error: Input file not found: {input_file}")
    sys.exit(1)

  with open(input_file, "r") as f:
    raw = f.read().strip()

  if not raw:
    print("[!] Error: Empty input file.")
    sys.exit(1)

  # Parse sequences from FASTA
  seqs: list[tuple[str, str]] = []
  current_header, current_seq = "", []
  for line in raw.splitlines():
    line = line.strip()
    if line.startswith(">"):
      if current_seq:
        seqs.append((current_header, "".join(current_seq)))
      current_header, current_seq = line[1:], []
    else:
      current_seq.append(line)
  if current_seq:
    seqs.append((current_header, "".join(current_seq)))

  num_seqs = len(seqs)
  if num_seqs < 2:
    print(f"[!] Error: At least 2 sequences required. Found {num_seqs}.")
    sys.exit(1)

  # --- Try boltz MSA first (single query seq only) ---
  if method in ("auto", "boltz") and num_seqs >= 2:
    query_seq = seqs[0][1].upper()
    if all(c in "ACDEFGHIKLMNPQRSTVWY" for c in query_seq):
      try:
        print("[*] Trying boltz (ColabFold MMseqs2) MSA search...")
        from boltz.data.msa.mmseqs2 import run_mmseqs2  # type: ignore[import-untyped]

        out_dir = Path(output_file).parent
        prefix = out_dir / Path(output_file).stem
        os.makedirs(str(prefix), exist_ok=True)

        a3m_lines = run_mmseqs2(
          query_seq,
          prefix=str(prefix / "msa_tmp"),
          use_env=True,
          use_filter=True,
          use_pairing=False,
          host_url="https://api.colabfold.com",
        )

        if a3m_lines:
          # Fuse into cleaned combined A3M
          from msa_boltz_search import _fuse_a3m_lines
          combined = _fuse_a3m_lines(query_seq, a3m_lines)
          Path(output_file).write_text(combined, encoding="utf-8")
          depth = combined.count(">") - 1
          method_used = "colabfold_mmseqs2_via_boltz"
          print(f"[*] Boltz MSA complete: {depth} sequences → {output_file}")
          print(f"[*] Method: {method_used}")
          return
        else:
          print("[!] Boltz MSA returned empty — falling back to EBI Clustal")
      except ImportError:
        print("[!] boltz not importable — falling back to EBI Clustal")
      except Exception as e:
        print(f"[!] Boltz MSA failed: {e} — falling back to EBI Clustal")

    elif method == "boltz":
      print("[!] boltz method requires a single valid query sequence")
      sys.exit(1)

  # --- Fallback: EBI Clustal Omega ---
  _align_ebi_clustal(input_file=input_file, output_file=output_file,
                     dry_run=dry_run, sequences=raw)


def _align_ebi_clustal(
    *, input_file: str, output_file: str, dry_run: bool = False,
    sequences: str | None = None,
) -> None:
  """EBI Clustal Omega alignment — original implementation."""
  if sequences is None:
    if not os.path.exists(input_file):
      print(f"[!] Error: Input file not found: {input_file}")
      sys.exit(1)
    max_size_bytes = 4 * 1024 * 1024
    file_size = os.path.getsize(input_file)
    if file_size > max_size_bytes:
      print(f"[!] Error: At most 4 MB file size supported. Found {file_size / (1024 * 1024):.2f} MB.")
      sys.exit(1)
    with open(input_file, "r") as f:
      sequences = f.read().strip()

  if not sequences:
    print("[!] Error: Empty input file.")
    sys.exit(1)

  num_sequences = sequences.count(">")
  if num_sequences > 4000:
    print(f"[!] Error: At most 4000 sequences supported. Found {num_sequences}.")
    sys.exit(1)

  print("[*] Submitting sequences to EBI Clustal Omega API...")

  user_email = os.environ.get("USER_EMAIL")
  if not user_email:
    print("[!] Error: USER_EMAIL environment variable is required.")
    sys.exit(1)
  data = _prepare_payload(user_email, "MSA", sequences)

  if dry_run:
    print(data)
    sys.exit(0)

  job_id = _CLIENT.fetch_text(
      "run", method="POST", data=data, headers={"Accept": "text/plain"}
  ).strip()
  print(f"[*] Job ID generated: {job_id}")

  print("[*] Polling server for completion...")
  start_time = time.time()
  while time.time() - start_time < _POLLING_TIMEOUT_SECS:
    status = _CLIENT.fetch_text(
        f"status/{job_id}", headers={"Accept": "text/plain"}, timeout=20
    ).strip()
    sys.stdout.write(".")
    sys.stdout.flush()
    if status == "FINISHED":
      print("\n[*] Job marked as FINISHED.")
      break
    elif status in ["ERROR", "FAILURE", "NOT_FOUND"]:
      print(f"\n[!] Job failed with status: {status}")
      sys.exit(1)
    time.sleep(10)
  else:
    print(f"\n[!] Job timed out after {_POLLING_TIMEOUT_SECS // 60} minutes.")
    sys.exit(1)

  print("\n[*] Job complete. Fetching results...\n")
  result_text = _CLIENT.fetch_text(f"result/{job_id}/fa", timeout=60)
  with open(output_file, "w") as f:
    f.write(result_text)
  print(f"[*] EBI Clustal Omega alignment saved to: {output_file}")
  print("[*] Method: ebi_clustal_omega")


def main() -> None:
  dotenv.load_dotenv(os.path.expanduser("~/.env"))
  parser = argparse.ArgumentParser(
      description="MSA computation — boltz (ColabFold MMseqs2) or EBI Clustal Omega"
  )
  parser.add_argument(
      "input", help="Path to FASTA file containing multiple sequences"
  )
  parser.add_argument(
      "-o",
      "--output",
      required=True,
      help="Path to save the output alignment file",
  )
  parser.add_argument(
      "--dry-run",
      action="store_true",
      help="Dry run: print payload and exit without submitting job",
  )
  parser.add_argument(
      "--method",
      choices=["auto", "boltz", "ebi"],
      default="auto",
      help="MSA method: auto (try boltz first), boltz (ColabFold MMseqs2 only), ebi (Clustal Omega only)",
  )
  args = parser.parse_args()

  _align_sequences(
      input_file=args.input, output_file=args.output,
      dry_run=args.dry_run, method=args.method,
  )


if __name__ == "__main__":
  main()
