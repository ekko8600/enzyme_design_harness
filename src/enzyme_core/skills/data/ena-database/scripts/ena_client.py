#!/usr/bin/env python3
"""Small file-oriented adapter for ENA Browser and Portal APIs."""
from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path


def request(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--accession", required=True); fetch.add_argument("--format", choices=["fasta", "json", "xml", "text"], default="fasta"); fetch.add_argument("--output", type=Path, required=True)
    search = sub.add_parser("search")
    search.add_argument("--result", required=True); search.add_argument("--query", required=True); search.add_argument("--fields", default="accession,description"); search.add_argument("--limit", type=int, default=100); search.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "fetch":
        url = f"https://www.ebi.ac.uk/ena/browser/api/{args.format}/{urllib.parse.quote(args.accession)}"
    else:
        params = urllib.parse.urlencode({"result": args.result, "query": args.query, "fields": args.fields, "limit": args.limit, "format": "json"})
        url = "https://www.ebi.ac.uk/ena/portal/api/search?" + params
    payload = request(url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    print(json.dumps({"status": "success", "source_url": url, "output": str(args.output), "bytes": len(payload)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
