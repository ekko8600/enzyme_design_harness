"""Atomic Skill API. Project state and research decisions belong to the caller."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time

from enzyme_core.contracts.models import ArtifactSpec, ExecutionContext, SkillRequest
from enzyme_core.contracts.validation import validate_artifacts
from enzyme_core.skills.registry import describe, discover, resolve_skill_file
from enzyme_core.support.config import load_config, resource_environment_for_skill, resource_status


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _interpreter(value: str | None) -> str:
    raw = value or sys.executable
    # Resolving a venv's Python symlink would select the base interpreter and
    # silently discard the explicitly selected environment's site-packages.
    return str(Path(shutil.which(raw) or raw).absolute())


def operation_for(skill_id: str, operation: str = "default") -> dict:
    profile = describe(skill_id)["execution_profile"]
    key = profile["default_operation"] if operation == "default" else operation
    if key not in profile["operations"]:
        raise ValueError("OPERATION_UNKNOWN:" + key)
    return {"id": key, **profile["operations"][key]}


def doctor(skill_id: str, operation: str = "default", *, python: str | None = None, resources: dict | None = None, resource_names=None, resource_groups=(), required_binaries=()) -> dict:
    op = operation_for(skill_id, operation)
    config = resources if resources is not None else load_config()
    checks = []
    executable = _interpreter(python)
    if op["kind"] in {"local_python", "host_authored"}:
        modules = op.get("requires_modules", [])
        if op["kind"] == "host_authored":
            from .runtime.interfaces import interface
            modules = interface(skill_id, op["id"])["requires_modules"]
        try:
            probe = subprocess.run([executable, "-c", "import importlib.util,json,sys; print(json.dumps({m:importlib.util.find_spec(m) is not None for m in json.loads(sys.argv[1])}))", json.dumps(modules)], capture_output=True, text=True, timeout=15)
            if probe.returncode:
                raise ValueError("INTERPRETER_PROBE_FAILED")
            availability = json.loads(probe.stdout)
            checks.extend({"kind": "python_module", "name": m, "available": bool(availability[m])} for m in modules)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            checks.append({"kind": "interpreter", "path": executable, "available": False, "reason": "interpreter probe failed"})
    if op["kind"].startswith("local_"):
        path = resolve_skill_file(op.get("target_skill", skill_id), op["path"])
        checks.append({"kind": "entrypoint", "path": str(path), "available": True})
    if op["kind"] == "local_executable":
        checks.append({"kind": "executable", "name": "bash", "available": shutil.which("bash") is not None})
    for section, items in (("model", config.get("model_weights", {}).get("models", {})), ("binary", config.get("runtime_binaries", {}))):
        for name, item in items.items():
            if skill_id in item.get("skills", []) and (name in resource_names if resource_names is not None else "requires_resources" not in op or name in op["requires_resources"]):
                checks.append({"kind": section, "name": name, **resource_status(item)})
    all_resources={**config.get('model_weights',{}).get('models',{}),**config.get('runtime_binaries',{})}
    for name in set(resource_names or [])-set(all_resources):checks.append({'kind':'resource','name':name,'available':False,'reason':'resource not declared'})
    for alternatives in resource_groups:
        rows={n:resource_status(all_resources[n]) if n in all_resources else {'available':False,'reason':'resource not declared'} for n in alternatives}
        checks.append({'kind':'resource_alternatives','names':alternatives,'available':any(r['available'] for r in rows.values()),'checks':rows})
    search_path=str(Path(executable).parent)+os.pathsep+os.environ.get('PATH','')
    for name in required_binaries:
        declared=config.get('runtime_binaries',{}).get(name)
        checks.append({'kind':'executable','name':name,**resource_status(declared)} if declared else {'kind':'executable','name':name,'available':shutil.which(name,path=search_path) is not None})
    return {"skill_id": skill_id, "operation": op["id"], "execution_kind": op["kind"], "python": executable,
            "environment_name": config.get("skills", {}).get(skill_id),
            "status": ("HOST_REQUIRED" if op["kind"] == "host_authored" else "AVAILABLE") if all(c["available"] for c in checks) else "UNAVAILABLE",
            "checks": checks, "scope": "declared dependencies and resource paths; does not validate CUDA, credentials, remote service or scientific accuracy"}


def prepare(request: SkillRequest, context: ExecutionContext) -> dict:
    description = describe(request.skill_id)
    op = operation_for(request.skill_id, request.operation)
    resources = context.resources if context.resources is not None else load_config()
    env = resource_environment_for_skill(resources, request.skill_id)
    env.update(context.environment)
    if context.credential_env_names is not None:
        import re
        for name in env:
            if re.search(r'(^|_)(API_KEY|TOKEN|PASSWORD|SECRET)(_|$)',name):
                raise ValueError('CREDENTIAL_VALUES_MUST_USE_ENV_REFERENCE:'+name)
    interpreter = _interpreter(context.python)
    env["PATH"] = str(Path(interpreter).parent) + os.pathsep + env.get("PATH", os.environ.get("PATH", ""))
    command = None
    entrypoint_sha = None
    if op["kind"] != "host_authored":
        path = resolve_skill_file(op.get("target_skill", request.skill_id), op["path"])
        if context.implementation:
            from enzyme_core.runtime.implementation import verify
            path = Path(verify(context.implementation, path))
        entrypoint_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        command = [interpreter, str(path)] if op["kind"] == "local_python" else ["bash", str(path)]
        command.extend(request.argv)
    elif request.argv:
        raise ValueError("HOST_OPERATION_DOES_NOT_ACCEPT_ARGV")
    artifact_specs = [{"path": s.path, "format": s.format, "required_keys": list(s.required_keys), "required_columns": list(s.required_columns)} for s in request.artifacts]
    prepared = {"schema_version": "enzyme-core/prepared-operation/v1", "skill_id": request.skill_id,
                "operation": op["id"], "execution_kind": op["kind"], "command": command,
                "work_dir": str(Path(context.work_dir).resolve()), "environment": env,
                "timeout_seconds": context.timeout_seconds, "artifacts": artifact_specs,
                "entrypoint_sha256": entrypoint_sha, "profile_sha256": _hash(description["execution_profile"]),
                "contract_sha256": _hash(description["scientific_contract"]), "resources_sha256": _hash(resources),
                "instruction_sha256": hashlib.sha256(description["instruction_markdown"].encode()).hexdigest(),
                "evidence_boundary": description["execution_profile"]["evidence_boundary"]}
    from enzyme_core.runtime.method_receipts import receipt_spec
    method=receipt_spec(op,list(request.argv),artifact_specs,work_dir=prepared["work_dir"])
    if method:prepared['method_receipt_spec']=method
    if context.credential_env_names is not None:prepared['credential_env_names']=list(context.credential_env_names)
    if context.native_isolation:prepared['native_isolation']={'reads':list(context.isolation_read_paths),'network':context.network,'python':interpreter}
    if context.implementation:
        prepared["implementation"] = context.implementation
    if command is None:
        prepared["instruction_markdown"] = description["instruction_markdown"]
        prepared["delivery"] = "Caller Host provides artifacts in work_dir; validate_delivery checks declared structure only."
    prepared["prepared_sha256"] = _hash(prepared)
    return prepared


def prepare_host_program(prepared, *, path, python, read_paths):
    _check_prepared(prepared)
    from enzyme_core.runtime.interfaces import interface
    spec=interface(prepared['skill_id'],prepared['operation'])
    if spec['kind']!='host_authored' or 'python' not in spec['host_modes']:raise ValueError('HOST_CODE_NOT_SUPPORTED')
    value=json.loads(json.dumps(prepared))
    value['host_program']={'path':str(Path(path).resolve()),'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),'python':_interpreter(python),'read_paths':[str(Path(p).resolve()) for p in read_paths]}
    value['prepared_sha256']=_hash({k:v for k,v in value.items() if k!='prepared_sha256'})
    from enzyme_core.runtime.host import validate
    validate(value)
    return value


def _check_prepared(prepared: dict) -> None:
    if prepared.get("schema_version") != "enzyme-core/prepared-operation/v1":
        raise ValueError("PREPARED_SCHEMA_UNSUPPORTED")
    value = {key: item for key, item in prepared.items() if key != "prepared_sha256"}
    if prepared.get("prepared_sha256") != _hash(value):
        raise ValueError("PREPARED_OPERATION_CHANGED")
    description = describe(prepared["skill_id"])
    if (_hash(description["execution_profile"]) != prepared["profile_sha256"] or
            _hash(description["scientific_contract"]) != prepared["contract_sha256"] or
            hashlib.sha256(description["instruction_markdown"].encode()).hexdigest() != prepared["instruction_sha256"]):
        raise ValueError("SKILL_DEFINITION_CHANGED")
    op = operation_for(prepared["skill_id"], prepared["operation"])
    if op["kind"] != prepared["execution_kind"]:
        raise ValueError("EXECUTION_KIND_CHANGED")
    if op["kind"] != "host_authored":
        path = resolve_skill_file(op.get("target_skill", prepared["skill_id"]), op["path"])
        if prepared.get("implementation"):
            from enzyme_core.runtime.implementation import verify
            path = Path(verify(prepared["implementation"], path))
        if hashlib.sha256(path.read_bytes()).hexdigest() != prepared["entrypoint_sha256"]:
            raise ValueError("ENTRYPOINT_CHANGED")
        if prepared["command"][1] != str(path):
            raise ValueError("ENTRYPOINT_PATH_CHANGED: expected="+str(path)+" actual="+str(prepared["command"][1]))


def validate_delivery(prepared: dict) -> dict:
    _check_prepared(prepared)
    if prepared.get('host_program'):
        from enzyme_core.runtime.host import validate
        validate(prepared)
    specs = tuple(ArtifactSpec(s["path"], s["format"], tuple(s["required_keys"]), tuple(s["required_columns"])) for s in prepared["artifacts"])
    import re
    forbidden=tuple(os.environ[n].encode() for n in prepared.get('credential_env_names',[]) if os.environ.get(n) and re.search(r'(^|_)(API_KEY|TOKEN|PASSWORD|SECRET)(_|$)',n))
    return validate_artifacts(Path(prepared["work_dir"]), specs,forbidden_values=forbidden)


def execute(prepared: dict, *, observer=None) -> dict:
    _check_prepared(prepared)
    if observer:
        observer({"event": "PREPARING", "prepared_sha256": prepared["prepared_sha256"]})
    if prepared["execution_kind"] == "host_authored" and not prepared.get("host_program"):
        return {"schema_version": "enzyme-core/run-result/v1", "skill_id": prepared["skill_id"], "operation": prepared["operation"], "execution_status": "HOST_REQUIRED", "validation_status": "NOT_REQUESTED", "scientific_validation": "NOT_ASSESSED", "prepared_sha256": prepared["prepared_sha256"]}
    root = Path(prepared["work_dir"])
    root.mkdir(parents=True, exist_ok=True)
    for artifact in prepared["artifacts"]:
        candidate = (root / artifact["path"]).resolve()
        if root.resolve() not in candidate.parents:
            raise ValueError("ARTIFACT_ESCAPES_WORK_DIRECTORY")
        if candidate.exists():
            raise ValueError("OUTPUT_ALREADY_EXISTS:" + artifact["path"])
    log_dir = root / ".enzyme-core-execution"
    log_dir.mkdir(exist_ok=False)  # A new operation uses a new directory; no replay/overwrite.
    for artifact in prepared['artifacts']:
        path=(root/artifact['path']).resolve()
        path.parent.mkdir(parents=True,exist_ok=True)
        if artifact['format']=='collection':path.mkdir(exist_ok=True)  # Parent creation for nested outputs may already have created it; all preexisting outputs were rejected above.
    started = time.time()
    status, code, error = "FAILED", None, None
    environment = os.environ.copy()
    environment.update(prepared["environment"])
    secrets=[]
    if 'credential_env_names' in prepared:
        from enzyme_core.runtime.credentials import environment as restricted_environment
        environment,secrets=restricted_environment(prepared['credential_env_names'],prepared['environment'],root)
    process = None
    with (log_dir / "stdout.log").open("wb") as stdout, (log_dir / "stderr.log").open("wb") as stderr:
        try:
            launch = prepared["command"]
            if prepared.get('host_program'):
                from enzyme_core.runtime.host import launch as host_launch
                launch=host_launch(prepared)
            if prepared.get("implementation"):
                from enzyme_core.runtime.isolation import command as isolated_command
                launch = isolated_command(launch, python=launch[0], reads=[prepared["implementation"]["root"]]+prepared["implementation"].get("read_paths", []), writes=[root], network=prepared["implementation"].get("network", False))
            if prepared.get('native_isolation') and not prepared.get('host_program'):
                from .runtime.isolation import command as isolated_command
                policy=prepared['native_isolation']
                launch=isolated_command(launch,python=policy['python'],reads=[Path(__file__).resolve().parent,Path(__file__).parent.parent,*policy['reads']],writes=[root],network=policy['network'],preserve_environment=True)
            process = subprocess.Popen(launch, cwd=root, env=environment, stdout=subprocess.PIPE if secrets else stdout, stderr=subprocess.PIPE if secrets else stderr, start_new_session=True)
            readers=[]
            if secrets:
                from enzyme_core.runtime.credentials import readers as redact_readers
                readers=redact_readers(process,stdout,stderr,secrets)
            if observer:
                try:
                    observer({"event": "RUNNING", "pid": process.pid,
                              "pgid": os.getpgid(process.pid) if os.name == "posix" else None,
                              "prepared_sha256": prepared["prepared_sha256"]})
                except BaseException:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait()
                    raise
            code = process.wait(timeout=prepared["timeout_seconds"])
            for reader in readers:reader.join(timeout=10)
            status = "COMPLETED" if code == 0 else "FAILED"
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()
            status, error = "TIMED_OUT", "operation exceeded timeout"
        except KeyboardInterrupt:
            if process and process.poll() is None:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
            status, error = "CANCELLED", "caller interrupted operation"
        except OSError as exc:
            error = str(exc)
    for reader in locals().get('readers',[]):reader.join(timeout=10)
    validation = validate_delivery(prepared) if status == "COMPLETED" else {"status": "NOT_RUN", "artifacts": []}
    from enzyme_core.runtime.method_receipts import verify as verify_method
    method=verify_method(root,prepared.get('method_receipt_spec')) if status=='COMPLETED' else {'status':'NOT_RUN','accepted':None}
    if method.get('accepted') is False:
        validation['status']='INVALID'
        error='METHOD_PREREQUISITE_NOT_SATISFIED' if method.get('diagnostic_only') else 'METHOD_EXECUTION_NOT_CONFIRMED'
    result = {"schema_version": "enzyme-core/run-result/v1", "skill_id": prepared["skill_id"], "operation": prepared["operation"],
              "execution_status": status, "returncode": code, "method_execution":method, "validation_status": validation["status"], "artifacts": validation["artifacts"],
              "scientific_validation": "NOT_ASSESSED", "prepared_sha256": prepared["prepared_sha256"], "elapsed_seconds": round(time.time() - started, 3),
              "stdout": str(log_dir / "stdout.log"), "stderr": str(log_dir / "stderr.log"), "error": error,
              "evidence_boundary": prepared["evidence_boundary"]}
    temporary = log_dir / "result.json.tmp"
    with temporary.open("w") as handle:
        handle.write(json.dumps(result, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(log_dir / "result.json")
    if observer:
        observer({"event": "FINISHED", "result": result})
    return result


__all__ = ["discover", "describe", "doctor", "prepare", "execute", "validate_delivery", "SkillRequest", "ExecutionContext", "ArtifactSpec"]


from enzyme_core.runtime.output_checks import verify_operation_output
