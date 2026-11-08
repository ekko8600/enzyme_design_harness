#!/usr/bin/env python3
"""Search a licensed/local HMDB XML export without scraping the website."""
from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path


def text(element: ET.Element, name: str) -> str:
    child = next((item for item in element if item.tag.rsplit("}", 1)[-1] == name), None)
    return (child.text or "").strip() if child is not None else ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Search a local HMDB metabolite XML export")
    parser.add_argument("--xml", type=Path, required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    needle = args.query.casefold()
    records = []
    for _event, element in ET.iterparse(args.xml, events=("end",)):
        if element.tag.rsplit("}", 1)[-1] != "metabolite":
            continue
        record = {field: text(element, field) for field in ("accession", "name", "chemical_formula", "smiles", "inchikey")}
        if needle in record["accession"].casefold() or needle in record["name"].casefold():
            records.append(record)
        element.clear()
        if len(records) >= args.limit:
            break
    payload = {"query": args.query, "source": str(args.xml), "count": len(records), "records": records}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "success", "count": len(records), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
