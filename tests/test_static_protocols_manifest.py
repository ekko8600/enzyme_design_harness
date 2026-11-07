from enzyme_core.skills.registry import resolve_skill_file
import importlib.util
import hashlib
import tempfile
import unittest
from pathlib import Path


SCRIPT = resolve_skill_file('pyrosetta-setup','scripts/static_protocols.py')
SPEC = importlib.util.spec_from_file_location("static_protocols_manifest", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class StaticProtocolsManifest(unittest.TestCase):
    def test_failed_algorithm_and_missing_decoys_are_not_complete(self):
        records=[{'path':'one.pdb','algorithm_move_succeeded':True},{'path':'two.pdb','algorithm_move_succeeded':False}]
        self.assertEqual(MODULE.method_execution(records,2)['status'],'FAILED')
        self.assertEqual(MODULE.method_execution(records[:1],2)['status'],'FAILED')
        records[1]['algorithm_move_succeeded']=True;records[1]['recovery_RMSD_A']=30
        self.assertEqual(MODULE.method_execution(records,2)['status'],'COMPLETED')
    def test_loaded_ligand_params_record_resolved_path_and_content_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "4PW.params"
            second = root / "ADP.params"
            first.write_text("NAME 4PW\n")
            second.write_text("NAME ADP\n")

            result = MODULE.parameter_provenance([first, second])

            self.assertEqual(
                result,
                [
                    {"path": str(first.resolve()), "sha256": hashlib.sha256(first.read_bytes()).hexdigest()},
                    {"path": str(second.resolve()), "sha256": hashlib.sha256(second.read_bytes()).hexdigest()},
                ],
            )

    def test_non_ligand_protocol_records_empty_loaded_parameter_list(self):
        self.assertEqual(MODULE.parameter_provenance(None), [])


if __name__ == "__main__":
    unittest.main()
