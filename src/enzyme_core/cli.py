from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from enzyme_core.api import ArtifactSpec, ExecutionContext, SkillRequest, describe, discover, doctor, execute, prepare, validate_delivery
from enzyme_core.skills.registry import resolve_skill_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Atomic enzyme Skill discovery and execution")
    sub = parser.add_subparsers(dest="command", required=True)
    find = sub.add_parser("discover"); find.add_argument("query", nargs="?", default="")
    details = sub.add_parser("describe"); details.add_argument("skill")
    path = sub.add_parser("path"); path.add_argument("skill"); path.add_argument("relative_path", nargs="?", default="SKILL.md")
    check = sub.add_parser("doctor"); check.add_argument("skill"); check.add_argument("--operation", default="default"); check.add_argument("--python")
    for name in ("prepare", "run"):
        item = sub.add_parser(name)
        item.add_argument("skill"); item.add_argument("--operation", default="default")
        item.add_argument("--work-dir", type=Path)
        item.add_argument("--python"); item.add_argument("--timeout", type=float, default=None)
        item.add_argument("--artifacts", type=Path, help="JSON array of ArtifactSpec objects")
    delivery = sub.add_parser("validate"); delivery.add_argument("prepared", type=Path)
    values = list(sys.argv[1:] if argv is None else argv)
    skill_args = []
    if "--" in values:
        split = values.index("--"); skill_args = values[split + 1:]; values = values[:split]
    args = parser.parse_args(values)
    try:
        if skill_args and args.command not in {"prepare", "run"}:
            raise ValueError("ARGV_ONLY_FOR_PREPARE_OR_RUN")
        if args.command == "discover":
            result = discover(args.query)
        elif args.command == "describe":
            result = describe(args.skill)
        elif args.command == "path":
            print(resolve_skill_file(args.skill, args.relative_path)); return 0
        elif args.command == "doctor":
            result = doctor(args.skill, args.operation, python=args.python)
        elif args.command == "validate":
            result = validate_delivery(json.loads(args.prepared.read_text()))
        else:
            if args.command == "run" and args.work_dir is None:
                raise ValueError("RUN_REQUIRES_WORK_DIR")
            specs = []
            if args.artifacts:
                for raw in json.loads(args.artifacts.read_text()):
                    specs.append(ArtifactSpec(raw["path"], raw.get("format", "file"), tuple(raw.get("required_keys", ())), tuple(raw.get("required_columns", ()))))
            request = SkillRequest(args.skill, args.operation, tuple(skill_args), tuple(specs))
            context = ExecutionContext(args.work_dir or Path.cwd(), python=args.python, timeout_seconds=args.timeout)
            result = prepare(request, context)
            if args.command == "run":
                result = execute(result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "doctor" and result["status"] == "UNAVAILABLE":
            return 2
        if args.command in {"run", "validate"} and (result.get("execution_status") in {"FAILED", "TIMED_OUT", "CANCELLED"} or result.get("validation_status", result.get("status")) == "INVALID"):
            return 1
        if isinstance(result, dict) and result.get("execution_status") == "HOST_REQUIRED":
            return 3
        return 0
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
