#!/usr/bin/env python3
"""Bounded CLI adapter for the BRENDA query helpers."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from brenda_queries import (
    compare_across_organisms,
    get_environmental_parameters,
    search_by_pattern,
    search_enzymes_by_product,
    search_enzymes_by_substrate,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    for name in ("search-substrate", "search-product", "search-pattern"):
        item = sub.add_parser(name)
        item.add_argument("query")
        item.add_argument("--limit", type=int, default=20, choices=range(1, 101), metavar="1..100")
        item.add_argument("--output", required=True)
    compare = sub.add_parser("compare-organisms")
    compare.add_argument("ec_number")
    compare.add_argument("--organism", action="append", required=True)
    compare.add_argument("--output", required=True)
    environment = sub.add_parser("environment")
    environment.add_argument("ec_number")
    environment.add_argument("--organism")
    environment.add_argument("--output", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    if args.command == "search-substrate":
        payload = search_enzymes_by_substrate(args.query, limit=args.limit)
    elif args.command == "search-product":
        payload = search_enzymes_by_product(args.query, limit=args.limit)
    elif args.command == "search-pattern":
        payload = search_by_pattern(args.query, limit=args.limit)
    elif args.command == "compare-organisms":
        payload = compare_across_organisms(args.ec_number, args.organism)
    else:
        payload = get_environmental_parameters(args.ec_number, organism=args.organism)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
