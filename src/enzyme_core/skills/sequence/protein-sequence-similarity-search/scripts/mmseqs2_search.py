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

"""Quick protein homologue search via ColabFold MMseqs2 API.

Submits a protein sequence to the ColabFold MMseqs2 server, downloads the
resulting MSA archive, parses the A3M alignment headers, and writes a styled
HTML report or Markdown companion table of sequence homologues sorted by
E-value to an output file specified via the required --output flag.
"""


import argparse
import html
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
from pathlib import Path

import sys
from pathlib import Path as _ProjectPath
PROJECT_ROOT = next(parent for parent in _ProjectPath(__file__).resolve().parents if (parent / "skills" / "registry.py").is_file()).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from enzyme_core.support import http_client

PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "skills" / "registry.py").is_file()).parent
if str(PROJECT_ROOT) not in sys.path:
  sys.path.insert(0, str(PROJECT_ROOT))

from enzyme_core.support.html_report_renderer import render_document

MAX_ALIGNMENT_HITS = 300
POLLING_TIMEOUT = 15 * 60  # 15 minutes.
COLABFOLD_HOST = "https://api.colabfold.com"
FASTA_COLUMNS = [
    "target",
    "bit_score",
    "identity",
    "e_value",
    "q_start",
    "q_end",
    "q_len",
    "t_start",
    "t_end",
    "t_len",
]
CLIENT = http_client.HttpClient(COLABFOLD_HOST, qps=2)


def read_sequence(query_input, tty=None):
  """Read sequence from a file path or raw string."""
  if os.path.isfile(query_input):
    print(f"[*] Reading sequence from file: {query_input}")
    if tty:
      print(f"[*] Reading sequence from file: {query_input}", file=tty)
    with open(query_input, "r") as f:
      sequence = f.read().strip()
      if sequence.startswith(">"):
        print("[*] Sequence is in FASTA format")
        if tty:
          print("[*] Sequence is in FASTA format", file=tty)
        sequence = "".join(sequence.split("\n")[1:])  # Remove FASTA header
      else:
        print("[*] Sequence is in raw format")  # No further processing needed
        if tty:
          print("[*] Sequence is in raw format", file=tty)
  else:
    print("[*] Using raw sequence string provided via command line.")
    sequence = query_input.strip()

  if not sequence:
    print("[!] Error: Empty sequence provided.")
    if tty:
      print("[!] Error: Empty sequence provided.", file=tty)
    sys.exit(1)

  return sequence


def parse_a3m(file_path, q_len):
  """Parse ColabFold-annotated A3M headers into hit dictionaries."""
  homologues = []

  with open(file_path, "r") as f:
    for line in f:
      if not line.startswith(">"):
        continue

      parts = line.strip().split()

      # Skip query header (no stat columns) or malformed lines
      if len(parts) < 10:
        continue

      try:
        hit = dict(zip(FASTA_COLUMNS, parts, strict=True))
        for col in ["q_start", "q_end", "q_len", "t_start", "t_end", "t_len"]:
          hit[col] = int(hit[col])
        for col in ["bit_score", "identity", "e_value"]:
          hit[col] = float(hit[col])

        # Query Coverage
        if q_len > 0 and hit["q_end"] > hit["q_start"]:
          aligned_residues = hit["q_end"] - hit["q_start"] + 1
          cov = min((aligned_residues / q_len) * 100, 100.0)
        else:
          cov = 0.0

        # Alignment length (target span)
        if hit["t_end"] > hit["t_start"]:
          aln_len = hit["t_end"] - hit["t_start"] + 1
        else:
          aln_len = 0

        hit |= {
            "target_id": hit["target"][1:],  # Strip leading '>'
            "q_cov": cov,
            "aln_len": aln_len,
        }
        homologues.append(hit)
      except (ValueError, IndexError):
        print(
            f"[!] Warning: Skipping malformed hit: {line.strip()}",
            file=sys.stderr,
        )
        continue

  homologues.sort(key=lambda x: x["e_value"])
  return homologues


def search_mmseqs2(
    query_input,
    output_report,
    json_file,
    include_mgnify=False,
    out_dir=None,
    max_hits=MAX_ALIGNMENT_HITS,
):
  """Submit sequence to ColabFold MMseqs2, parse results, write output."""
  original_stdout = sys.stdout
  report_buffer = _ReportBuffer()
  sys.stdout = report_buffer

  try:
    _run_mmseqs2(
        query_input,
        json_file,
        include_mgnify,
        tty=original_stdout,
        out_dir=out_dir,
        max_hits=max_hits,
    )
  finally:
    sys.stdout = original_stdout
  _write_report(output_report, report_buffer.getvalue(), "MMseqs2 Sequence Homologue Report")

  print(f"[*] Results saved to: {output_report}")
  html_path = _html_path(output_report)
  if html_path != output_report:
    print(f"[*] HTML report saved to: {html_path}")
  if json_file:
    print(f"[*] Results saved to: {json_file}")
  print("...done!")


def _run_mmseqs2(query_input, json_file, include_mgnify, tty, out_dir=None, max_hits=MAX_ALIGNMENT_HITS):
  """Internal: runs the actual MMseqs2 search with print going to file."""
  sequence = read_sequence(query_input, tty=tty)
  q_len = len(sequence)

  # --- 1. Submit ---
  print("[*] Search method: MMseqs2 (ColabFold API)")
  print(
      f"[*] Submitting sequence ({q_len} residues) to ColabFold MMseqs2 API..."
  )
  print("[*] Search method: MMseqs2 (ColabFold API)", file=tty)

  query_fasta = f">Query_1\n{sequence}\n"
  data = urllib.parse.urlencode({
      "q": query_fasta,
      "mode": "all",
  }).encode("ascii")

  try:
    ticket = CLIENT.fetch_json(
        f"{COLABFOLD_HOST}/ticket/msa", method="POST", data=data, timeout=60
    )
  except (
      urllib.error.URLError,
      urllib.error.HTTPError,
      TimeoutError,
      RuntimeError,
  ) as e:
    print(f"[!] MMseqs2 API Submission Failed: {e}")
    print(f"[!] MMseqs2 API Submission Failed: {e}", file=tty)
    sys.exit(2)

  ticket_id = ticket.get("id")
  if not ticket_id:
    status = ticket.get("status", "UNKNOWN")
    if status == "RATELIMIT":
      print("[!] MMseqs2 rate limit hit on submission.")
      print("[!] MMseqs2 rate limit hit on submission.", file=tty)
    else:
      print(f"[!] MMseqs2 submission failed: {ticket}")
      print(f"[!] MMseqs2 submission failed: {ticket}", file=tty)
    sys.exit(2)

  print(f"[*] Ticket ID generated: {ticket_id}")
  print(f"[*] Ticket ID: {ticket_id}", file=tty)

  # --- 2. Poll ---
  print("[*] Polling server for completion...")
  print("[*] Polling server for completion...", file=tty)

  start_time = time.monotonic()
  while time.monotonic() - start_time < POLLING_TIMEOUT:
    state = CLIENT.fetch_json(
        f"{COLABFOLD_HOST}/ticket/{ticket_id}", timeout=20
    ).get("status", "UNKNOWN")

    # States below are taken from the original ColabFold code:
    # https://github.com/sokrypton/ColabFold/blob/main/colabfold/colabfold.py:201
    if state == "COMPLETE":
      print("\n[*] Job finished successfully!")
      print("\n[*] Job finished successfully!", file=tty)
      break

    elif state in ("ERROR", "MAINTENANCE"):
      print(f"[!] MMseqs2 job failed with status: {state}")
      print(f"[!] MMseqs2 job failed with status: {state}", file=tty)
      sys.exit(2)

    elif state == "RATELIMIT":
      print("[!] Rate limit hit. Waiting...")
      print("[!] Rate limit hit. Waiting...", file=tty)

    elif state in ("PENDING", "RUNNING", "UNKNOWN"):
      sys.stdout.write(".")
      sys.stdout.flush()
      tty.write(".")
      tty.flush()
    time.sleep(10)

  else:
    print("[!] Polling timed out.")
    print("[!] Polling timed out.", file=tty)
    sys.exit(2)

  # --- 3. Download & Extract ---
  print("[*] Downloading and extracting MSA files...")
  tmp_dir = tempfile.mkdtemp(prefix="mmseqs2_")
  tar_path = os.path.join(tmp_dir, f"{ticket_id}.tar.gz")

  try:
    raw_data = CLIENT.fetch_bytes(
        f"{COLABFOLD_HOST}/result/download/{ticket_id}", timeout=120
    )
    with open(tar_path, "wb") as f:
      f.write(raw_data)

    with tarfile.open(tar_path, "r:gz") as tar:
      for member in tar.getmembers():
        if member.name.startswith("/") or ".." in member.name:
          continue
        tar.extract(member, path=tmp_dir)

    os.remove(tar_path)
  except (
      urllib.error.URLError,
      urllib.error.HTTPError,
      TimeoutError,
      RuntimeError,
      OSError,
      tarfile.ReadError,
  ) as e:
    print(f"[!] Failed to download/extract results: {e}")
    print(f"[!] Failed to download/extract results: {e}", file=tty)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    sys.exit(2)

  # --- 4. Parse A3M files ---
  all_homologues = []
  a3m_sources = []

  uniref_path = os.path.join(tmp_dir, "uniref.a3m")
  if os.path.exists(uniref_path):
    uniref_hits = parse_a3m(uniref_path, q_len)
    print(f"[*] Parsed {len(uniref_hits)} hits from uniref.a3m")
    all_homologues.extend(uniref_hits)
    a3m_sources.append(uniref_path)
  else:
    print("[!] Warning: uniref.a3m not found in results archive.")

  if include_mgnify:
    mgnify_path = os.path.join(tmp_dir, "bfd.mgnify30.metaeuk30.smag30.a3m")
    if os.path.exists(mgnify_path):
      mgnify_hits = parse_a3m(mgnify_path, q_len)
      print(f"[*] Parsed {len(mgnify_hits)} hits from mgnify a3m")
      all_homologues.extend(mgnify_hits)
      a3m_sources.append(mgnify_path)
    else:
      print("[!] Warning: mgnify a3m file not found in results archive.")
      print(
          "[!] Warning: mgnify a3m file not found in results archive.", file=tty
      )

  if not all_homologues:
    print("[!] No homologues found.")
    print("[!] No homologues found.", file=tty)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    sys.exit(0)

  # Sort combined results by E-value, take top hits
  all_homologues.sort(key=lambda x: x["e_value"])
  all_homologues = all_homologues[:max_hits]

  if out_dir:
    artifacts = write_alignment_artifacts(
        Path(out_dir), a3m_sources, q_len=q_len, max_hits=max_hits
    )
    for key, value in artifacts.items():
      print(f"[*] {key}: {value}")

  # Cleanup temp directory after optional artifact export.
  shutil.rmtree(tmp_dir, ignore_errors=True)

  # --- 5. Save JSON ---
  if json_file:
    with open(json_file, "w") as f:
      json.dump(all_homologues, f, indent=4)
    print(f"[*] JSON results successfully saved to: {json_file}")

  # --- 6. Output Markdown Table ---
  print(f"\n### Top {len(all_homologues)} Sequence Homologues (MMseqs2)")
  print("| Target ID | Q-Cov | E-value | Seq Identity(%) | Aln Length |")
  print("|---|---|---|---|---|")

  for hit in all_homologues:
    target_id = hit["target_id"]
    q_cov = f"{hit['q_cov']:.1f}%"
    e_value = f"{hit['e_value']:.2e}"
    identity = f"{hit['identity'] * 100:.1f}%"
    aln_len = str(hit["aln_len"])

    print(f"| {target_id} | {q_cov} | {e_value} | {identity} | {aln_len} |")


def read_a3m_records(path):
  records = []
  header = None
  seq_lines = []
  with open(path, "r", encoding="utf-8") as handle:
    for raw in handle:
      line = raw.strip()
      if not line:
        continue
      if line.startswith(">"):
        if header is not None:
          records.append((header, "".join(seq_lines)))
        header = line[1:].strip() or f"sequence_{len(records) + 1}"
        seq_lines = []
      else:
        seq_lines.append(line)
  if header is not None:
    records.append((header, "".join(seq_lines)))
  return records


def strip_a3m_insertions(seq):
  return "".join(ch.upper() for ch in seq if not ch.islower() and ch != ".")


def write_fasta_records(path, records):
  with open(path, "w", encoding="utf-8") as handle:
    for header, seq in records:
      handle.write(f">{header}\n")
      handle.write(f"{seq}\n")


def write_alignment_artifacts(out_dir: Path, a3m_sources, q_len, max_hits):
  out_dir.mkdir(parents=True, exist_ok=True)
  raw_a3m = out_dir / "combined_raw.a3m"
  cleaned_a3m = out_dir / "combined_cleaned.a3m"
  homolog_fasta = out_dir / "homologs_for_msa.fasta"
  seen_headers = set()
  raw_records = []
  cleaned_records = []
  for source in a3m_sources:
    for header, seq in read_a3m_records(source):
      key = header.split()[0]
      if key in seen_headers:
        continue
      seen_headers.add(key)
      raw_records.append((header, seq))
      cleaned_records.append((header, strip_a3m_insertions(seq)))
      if len(cleaned_records) >= max_hits + 1:
        break
    if len(cleaned_records) >= max_hits + 1:
      break
  if cleaned_records:
    query_header, query_seq = cleaned_records[0]
    if len(query_seq.replace("-", "")) != q_len:
      print(
          "[!] Warning: cleaned query length differs from input sequence length.",
          file=sys.stderr,
      )
    write_fasta_records(raw_a3m, raw_records)
    write_fasta_records(cleaned_a3m, cleaned_records)
    write_fasta_records(homolog_fasta, cleaned_records)
  return {
      "raw_a3m": str(raw_a3m),
      "cleaned_a3m": str(cleaned_a3m),
      "homolog_fasta": str(homolog_fasta),
  }


def main():
  parser = argparse.ArgumentParser(
      description=(
          "Quick protein homologue search via ColabFold MMseqs2 API. "
          "Exits with code 2 on API failures to signal fallback to BLAST."
      )
  )
  parser.add_argument(
      "query_input", help="Path to a FASTA file or raw sequence string"
  )
  parser.add_argument(
      "-o",
      "--output",
      required=True,
      help="Path to save the output report. Prefer .html; .md also creates a same-name .html companion.",
  )
  parser.add_argument(
      "-j",
      "--json",
      help="Path to save the output JSON file (optional)",
      default=None,
  )
  parser.add_argument(
      "--include-mgnify",
      action="store_true",
      help="Also parse and include hits from the mgnify/environmental database",
  )
  parser.add_argument(
      "--out-dir",
      help="Directory to save raw/cleaned A3M and homolog FASTA artifacts",
  )
  parser.add_argument(
      "--max-hits",
      type=int,
      default=MAX_ALIGNMENT_HITS,
      help="Maximum non-query hits to keep in reports and exported alignments",
  )
  args = parser.parse_args()

  print(f"[*] Output: {args.output}")
  if args.json:
    print(f"[*] Output: {args.json}")

  search_mmseqs2(
      args.query_input,
      args.output,
      args.json,
      args.include_mgnify,
      args.out_dir,
      args.max_hits,
  )


class _ReportBuffer:
  def __init__(self):
    self.parts = []

  def write(self, text):
    self.parts.append(text)

  def flush(self):
    pass

  def getvalue(self):
    return "".join(self.parts)


def _write_report(output_report, markdown, title):
  if output_report.lower().endswith(".html"):
    with open(output_report, "w", encoding="utf-8") as handle:
      handle.write(_render_html(markdown, title))
    return
  with open(output_report, "w", encoding="utf-8") as handle:
    handle.write(markdown)
  with open(_html_path(output_report), "w", encoding="utf-8") as handle:
    handle.write(_render_html(markdown, title))


def _html_path(output_report):
  root, _ = os.path.splitext(output_report)
  return output_report if output_report.lower().endswith(".html") else f"{root}.html"


def _render_html(markdown, title):
  body = _render_markdown(markdown)
  return render_document(
      title=title,
      subtitle="MMseqs2 sequence homology search results for enzyme mutation design.",
      body_html=f'<section class="section">{body}</section>',
      kicker="MMseqs2 Search",
  )


def _render_markdown(markdown):
  lines = markdown.splitlines()
  out = []
  in_table = False
  for line in lines:
    stripped = line.strip()
    if not stripped:
      if in_table:
        out.append("</tbody></table>")
        in_table = False
      continue
    if stripped.startswith("### "):
      if in_table:
        out.append("</tbody></table>")
        in_table = False
      out.append(f"<h2>{html.escape(stripped[4:])}</h2>")
    elif stripped.startswith("[*]") or stripped.startswith("[!]"):
      if in_table:
        out.append("</tbody></table>")
        in_table = False
      out.append(f"<p class=\"log\">{html.escape(stripped)}</p>")
    elif stripped.startswith("|") and stripped.endswith("|"):
      cells = [cell.strip() for cell in stripped.strip("|").split("|")]
      if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
        continue
      tag = "th" if not in_table else "td"
      if not in_table:
        out.append("<table><tbody>")
        in_table = True
      out.append("<tr>" + "".join(f"<{tag}>{html.escape(cell)}</{tag}>" for cell in cells) + "</tr>")
    else:
      if in_table:
        out.append("</tbody></table>")
        in_table = False
      out.append(f"<p>{html.escape(stripped)}</p>")
  if in_table:
    out.append("</tbody></table>")
  return "\n".join(out)


def _css():
  return """
body { margin:0; background:#eef3f7; color:#17202f; font-family:Arial, "Noto Sans", sans-serif; }
.report { max-width:1100px; margin:24px auto; background:#fff; border:1px solid #dbe3ec; border-top:5px solid #26547c; border-radius:8px; padding:28px; box-shadow:0 2px 12px rgba(15,23,42,.06); }
h2 { margin-top:0; font-size:24px; }
.log { color:#475569; font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
table { width:100%; border-collapse:collapse; margin:16px 0; font-size:14px; }
th, td { border:1px solid #dbe3ec; padding:9px 10px; text-align:left; vertical-align:top; }
th { background:#f1f5f9; color:#334155; }
@media (max-width:760px) { .report { margin:0; border-radius:0; padding:18px; } table { display:block; overflow-x:auto; } }
"""


if __name__ == "__main__":
  main()
