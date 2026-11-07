"""Read-only JSONL Skill discovery endpoint for external Harnesses."""
from __future__ import annotations
import argparse, json, re, sys
from typing import Any
from enzyme_core.skills.registry import describe, discover

SCHEMA = "enzyme-agent/skill-discovery-request/v1"
RESPONSE = "enzyme-agent/skill-discovery-response/v1"
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


def handle(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA:
        raise ValueError("SKILL_DISCOVERY_SCHEMA_UNSUPPORTED")
    request_id, operation = value.get("request_id"), value.get("operation")
    if not isinstance(request_id, str) or not _ID.fullmatch(request_id):
        raise ValueError("SKILL_DISCOVERY_REQUEST_ID_INVALID")
    payload = value.get("payload", {})
    if not isinstance(payload, dict):
        raise ValueError("SKILL_DISCOVERY_PAYLOAD_INVALID")
    if operation == "skill_discover":
        query = payload.get("query", "")
        if not isinstance(query, str): raise ValueError("SKILL_DISCOVERY_QUERY_INVALID")
        skills = discover(query)
        result = {"query": query, "skills": skills, "count": len(skills)}
    elif operation == "skill_describe":
        skill_id = payload.get("skill_id")
        if not isinstance(skill_id, str) or not skill_id: raise ValueError("SKILL_DISCOVERY_SKILL_ID_REQUIRED")
        result = describe(skill_id)
    else:
        raise ValueError("SKILL_DISCOVERY_OPERATION_UNSUPPORTED")
    return {"schema_version": RESPONSE, "request_id": request_id, "status": "COMPLETED", **result}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.parse_args()
    for line in sys.stdin:
        try:
            print(json.dumps(handle(json.loads(line)), ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({"schema_version": RESPONSE, "status": "FAILED",
                              "error": {"code": str(exc), "message": str(exc)}}, ensure_ascii=False), flush=True)
    return 0

if __name__ == "__main__": raise SystemExit(main())
