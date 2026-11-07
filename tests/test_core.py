from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from enzyme_core.api import ArtifactSpec, ExecutionContext, SkillRequest, describe, doctor, execute, prepare, validate_delivery
from enzyme_core.analysis.calibration import evaluate_calibration
from enzyme_core.contracts.validation import validate_artifacts
from enzyme_core.skills.registry import list_skills, resolve_skill_file
from enzyme_core.support import brenda_client
from enzyme_core.support.http_client import HttpClient


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_catalog_and_alias_operation(self):
        self.assertEqual(len(list_skills()), 95)
        self.assertNotIn("reflect-skill-experience", {x["id"] for x in list_skills()})
        prepared = prepare(SkillRequest("uniprot", argv=("--help",)), ExecutionContext(self.root))
        self.assertIn("uniprot-database", prepared["command"][1])
        self.assertEqual(describe("enzyme-qm-ts")["execution_profile"]["default_operation"], "selfcheck")

    def test_cannot_resolve_another_skill_by_traversal(self):
        with self.assertRaises(ValueError):
            resolve_skill_file("rdkit", "../../data/pubmed-database/SKILL.md")

    def test_report_execution_validates_real_output(self):
        request = SkillRequest("html-report-renderer", argv=("--body", "<p>Observation</p>", "--output", "report.html"), artifacts=(ArtifactSpec("report.html", "html"),))
        result = execute(prepare(request, ExecutionContext(self.root)))
        self.assertEqual(result["execution_status"], "COMPLETED")
        self.assertEqual(result["validation_status"], "VALID")
        self.assertEqual(result["scientific_validation"], "NOT_ASSESSED")
        self.assertEqual(len(result["artifacts"][0]["sha256"]), 64)

    def test_nested_collection_and_member_create_parents_in_either_order(self):
        for index, reverse in enumerate([False, True]):
            artifacts=[ArtifactSpec('outputs/report.html','html'),ArtifactSpec('outputs','collection')]
            if reverse:artifacts.reverse()
            request=SkillRequest('html-report-renderer',argv=('--body','<p>evidence</p>','--output','outputs/report.html'),artifacts=tuple(artifacts))
            result=execute(prepare(request,ExecutionContext(self.root/str(index))))
            self.assertEqual(result['execution_status'],'COMPLETED')
            self.assertEqual(result['validation_status'],'VALID')

    def test_successful_process_does_not_invent_missing_outputs(self):
        request = SkillRequest("html-report-renderer", argv=("--help",), artifacts=(ArtifactSpec("report.html", "html"),))
        result = execute(prepare(request, ExecutionContext(self.root)))
        self.assertEqual(result["execution_status"], "COMPLETED")
        self.assertEqual(result["validation_status"], "INVALID")

    def test_success_without_artifact_contract_is_unvalidated(self):
        result = execute(prepare(SkillRequest("html-report-renderer", argv=("--help",)), ExecutionContext(self.root)))
        self.assertEqual(result["validation_status"], "NOT_REQUESTED")

    def test_replay_does_not_overwrite_execution(self):
        prepared = prepare(SkillRequest("html-report-renderer", argv=("--help",)), ExecutionContext(self.root))
        execute(prepared)
        with self.assertRaises(FileExistsError):
            execute(prepared)

    def test_preexisting_output_cannot_certify_new_execution(self):
        (self.root / "report.html").write_text("<html>old</html>")
        prepared = prepare(SkillRequest("html-report-renderer", argv=("--help",), artifacts=(ArtifactSpec("report.html", "html"),)), ExecutionContext(self.root))
        with self.assertRaisesRegex(ValueError, "OUTPUT_ALREADY_EXISTS"):
            execute(prepared)

    def test_native_isolation_cannot_write_attempt_parent(self):
        work=self.root/'attempt';work.mkdir()
        prepared=prepare(SkillRequest('html-report-renderer',operation='render_html',argv=('--body','<p>scope</p>','--output','../escaped.html'),artifacts=(ArtifactSpec('page.html','html'),)),
                         ExecutionContext(work,native_isolation=True,isolation_read_paths=(str(self.root),),credential_env_names=()))
        result=execute(prepared)
        self.assertEqual(result['execution_status'],'FAILED',result)
        self.assertFalse((self.root/'escaped.html').exists())

    def test_changed_preparation_is_rejected(self):
        prepared = prepare(SkillRequest("html-report-renderer", argv=("--help",)), ExecutionContext(self.root))
        prepared["command"].append("--body")
        with self.assertRaisesRegex(ValueError, "PREPARED_OPERATION_CHANGED"):
            execute(prepared)

    def test_host_skill_requires_delivery_without_fabricated_execution(self):
        specs = (ArtifactSpec("hypothesis.json", "json", ("hypothesis", "prediction")),)
        prepared = prepare(SkillRequest("hypothesis-generation", artifacts=specs), ExecutionContext(self.root))
        self.assertEqual(execute(prepared)["execution_status"], "HOST_REQUIRED")
        self.assertEqual(validate_delivery(prepared)["status"], "INVALID")
        (self.root / "hypothesis.json").write_text(json.dumps({"hypothesis": "H", "prediction": "P"}))
        self.assertEqual(validate_delivery(prepared)["status"], "VALID")
        self.assertEqual(doctor("hypothesis-generation")["status"], "HOST_REQUIRED")

    def test_bad_cli_arguments_return_failed_execution(self):
        result = execute(prepare(SkillRequest("html-report-renderer", argv=("--does-not-exist",)), ExecutionContext(self.root)))
        self.assertEqual(result["execution_status"], "FAILED")
        self.assertEqual(result["validation_status"], "NOT_RUN")

    @unittest.skipUnless(os.name == "posix", "process group timeout is POSIX-specific")
    def test_timeout_kills_operation_and_does_not_validate_outputs(self):
        interpreter = self.root / "slow-python"
        interpreter.write_text("#!/bin/sh\nsleep 30 &\nwait\n")
        interpreter.chmod(0o700)
        prepared = prepare(SkillRequest("html-report-renderer", argv=("--help",)), ExecutionContext(self.root / "run", python=str(interpreter), timeout_seconds=.1))
        result = execute(prepared)
        self.assertEqual(result["execution_status"], "TIMED_OUT")
        self.assertEqual(result["validation_status"], "NOT_RUN")
        self.assertLess(result["elapsed_seconds"], 5)

    def test_required_fields_and_finite_pdb_coordinates(self):
        (self.root / "a.json").write_text('{"observation":{}}')
        spec = ArtifactSpec("a.json", "json", ("observation.value",))
        self.assertEqual(validate_artifacts(self.root, (spec,))["status"], "INVALID")
        (self.root / "a.json").write_text('{"observation":{"value":NaN}}')
        self.assertEqual(validate_artifacts(self.root, (spec,))["status"], "INVALID")
        (self.root / "a.csv").write_text("variant,value\nWT,1\n")
        self.assertEqual(validate_artifacts(self.root, (ArtifactSpec("a.csv", "csv", required_columns=("unit",)),))["status"], "INVALID")
        (self.root / "a.fasta").write_text(">one\n>two\nAAA\n")
        self.assertEqual(validate_artifacts(self.root, (ArtifactSpec("a.fasta", "fasta"),))["status"], "INVALID")
        atom = "ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 20.00           C\n"
        (self.root / "a.pdb").write_text(atom[:30] + "     nan" + atom[38:])
        self.assertEqual(validate_artifacts(self.root, (ArtifactSpec("a.pdb", "pdb"),))["status"], "INVALID")

    def test_symlink_output_escape_rejected(self):
        outside = self.root / "outside"; outside.mkdir()
        (outside / "a.json").write_text("{}")
        work = self.root / "work"; work.mkdir()
        (work / "alias").symlink_to(outside, target_is_directory=True)
        result = validate_artifacts(work, (ArtifactSpec("alias/a.json", "json"),))
        self.assertEqual(result["status"], "INVALID")

    def test_tied_scores_do_not_create_perfect_average_precision(self):
        for labels in ([0, 1], [1, 0]):
            self.assertEqual(evaluate_calibration(labels, [.5, .5])["auprc"], .5)
        self.assertEqual(evaluate_calibration([1, 0, 1, 0], [.9, .8, .7, .1])["auroc"], .75)

    def test_cli_preserves_native_args_after_separator(self):
        result = subprocess.run([sys.executable, "-m", "enzyme_core", "run", "html-report-renderer", "--work-dir", str(self.root), "--", "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["execution_status"], "COMPLETED")

    def test_cli_discovery_returns_successful_list(self):
        result = subprocess.run([sys.executable, "-m", "enzyme_core", "discover"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)), 95)

    def test_doctor_bad_interpreter_is_explicitly_unavailable(self):
        result = doctor("rdkit", python=str(self.root / "nonexistent"))
        self.assertEqual(result["status"], "UNAVAILABLE")

    def test_http_adapter_preserves_default_and_request_headers(self):
        client = HttpClient("https://example.invalid", default_headers={"Origin": "https://screen.invalid", "Accept": "text/plain"})
        from email.message import Message
        class Response:
            status = 200
            headers = Message()
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size=-1): return b'{"value":1}'
        with patch("enzyme_core.support.http_client.urlopen", return_value=Response()) as fetch:
            result = client.fetch_json("/query", headers={"Accept": "application/json"})
        self.assertEqual(result, {"value": 1})
        request = fetch.call_args.args[0]
        self.assertEqual(request.get_header("Origin"), "https://screen.invalid")
        self.assertEqual(request.get_header("Accept"), "application/json")

    def test_brenda_contract_and_missing_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "BRENDA_CREDENTIALS_REQUIRED"):
                brenda_client.get_km_values("1.1.1.1")
        with patch.object(brenda_client, "call_brenda", return_value=["kmValue*1!kmValue*2"]) as remote:
            self.assertEqual(brenda_client.get_km_values("1.1.1.1", organism="test", substrate="s"), ["kmValue*1", "kmValue*2"])
            action, fields = remote.call_args.args
            self.assertEqual(action, "getKmValue")
            self.assertIn("organism*test", fields)
            self.assertIn("substrate*s", fields)


if __name__ == "__main__":
    unittest.main()

class HarnessCoreExtensionTests(unittest.TestCase):
    def test_lifecycle_observer_receipt_and_large_file_streaming(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = []
            prepared = prepare(SkillRequest('html-report-renderer', argv=('--body', '<p>actual</p>', '--output', 'page.html'),
                                            artifacts=(ArtifactSpec('page.html', 'html'),)), ExecutionContext(root))
            result = execute(prepared, observer=events.append)
            self.assertEqual([e['event'] for e in events], ['PREPARING', 'RUNNING', 'FINISHED'])
            self.assertEqual(result['validation_status'], 'VALID')
            data = root / 'trajectory.bin'
            data.write_bytes(b'x' * (2 * 1024 * 1024 + 1))
            original = Path.read_bytes
            def guarded(path):
                if path == data:
                    raise AssertionError('generic binary validation must stream')
                return original(path)
            with patch.object(Path, 'read_bytes', guarded):
                self.assertEqual(validate_artifacts(root, (ArtifactSpec('trajectory.bin'),))['status'], 'VALID')

class InterpreterEnvironmentTests(unittest.TestCase):
    def test_selected_venv_module_is_probed_in_that_environment(self):
        import venv
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            venv.EnvBuilder(with_pip=False, symlinks=True).create(root / 'venv')
            interpreter = root / 'venv/bin/python'
            site = subprocess.check_output([str(interpreter), '-c', 'import site;print(site.getsitepackages()[0])'], text=True).strip()
            (Path(site) / 'harness_environment_sentinel.py').write_text('VALUE = 1\n')
            with patch('enzyme_core.api.operation_for', return_value={'id': 'render_html', 'kind': 'local_python', 'path': 'scripts/render_html.py',
                                                                     'requires_modules': ['harness_environment_sentinel']}):
                result = doctor('html-report-renderer', python=str(interpreter))
            self.assertEqual(result['status'], 'AVAILABLE')
            self.assertEqual(result['python'], str(interpreter))

class StructureHandoffTests(unittest.TestCase):
    def test_explicit_candidate_export_and_tamper_rejection(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            predictions = root / 'predictions'
            predictions.mkdir()
            pdb = predictions / 'candidate.pdb'
            pdb.write_text('ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 20.00           C\n')
            manifest = predictions / 'manifest.json'
            manifest.write_text(json.dumps({'schema_version': 'enzyme-core/structure-prediction/v1', 'input_fasta_sha256': 'fasta-hash',
                                            'structures': [{'path': pdb.name, 'sha256': hashlib.sha256(pdb.read_bytes()).hexdigest()}]}))
            request = SkillRequest('alphafold', 'select_structure', ('--manifest', str(manifest), '--candidate', 'candidate.pdb'),
                                   (ArtifactSpec('selected.pdb', 'pdb'), ArtifactSpec('selection.json', 'json', ('structure_sha256',))))
            result = execute(prepare(request, ExecutionContext(root / 'selected')))
            self.assertEqual(result['validation_status'], 'VALID')
            pdb.write_text('changed')
            failed = execute(prepare(request, ExecutionContext(root / 'tampered')))
            self.assertEqual(failed['execution_status'], 'FAILED')
            self.assertEqual(doctor('alphafold', 'select_structure')['status'], 'AVAILABLE')
