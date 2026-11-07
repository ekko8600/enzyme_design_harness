"""A published checkout must ship the source its local-execution Skills run."""
import unittest
from pathlib import Path

from enzyme_core.support.config import ROOT, load_config, resource_status


class VendorSources(unittest.TestCase):
    def setUp(self):
        self.vendor = ROOT / "vendor"
        self.config = load_config()

    def test_mit_sources_and_licenses_are_present(self):
        expected = {
            "ligandmpnn/run.py": 1000,
            "ligandmpnn/model_utils.py": 1000,
            "ligandmpnn/data_utils.py": 1000,
            "ligandmpnn/openfold/utils/feats.py": 100,
            "ligandmpnn/LICENSE": 500,
            "ligandmpnn/openfold/LICENSE": 5000,
            "ligandmpnn/THIRD_PARTY_NOTICES.md": 100,
            "mut_model/model_run/finetune_online_distill.py": 10000,
            "mut_model/model_run/models/model_direct.py": 1000,
            "mut_model/configs/online_distill.yaml": 100,
            "mut_model/LICENSE": 500,
        }
        for relative, minimum in expected.items():
            path = self.vendor / relative
            self.assertTrue(path.is_file(), relative)
            self.assertGreaterEqual(path.stat().st_size, minimum, relative)

    def test_no_weights_or_datasets_are_bundled(self):
        for pattern in ("ligandmpnn/model_params", "mut_model/pretrain_weight/*.pt",
                        "mut_model/finetune_weight/*.pt", "mut_model/data/*.csv"):
            self.assertEqual(list(self.vendor.glob(pattern)), [], pattern)

    def test_no_bytecode_or_vcs_metadata_is_bundled(self):
        for pattern in ("**/__pycache__", "**/*.pyc", "**/.git", "**/.gitmodules"):
            self.assertEqual(list(self.vendor.glob(pattern)), [], pattern)

    def test_resources_resolve_the_bundled_copies(self):
        models = self.config["model_weights"]["models"]
        self.assertEqual(Path(models["mut_model"]["path"]), self.vendor / "mut_model")
        self.assertEqual(Path(models["mut_model"]["env_vars"]["MUT_MODEL_HOME"]),
                         self.vendor / "mut_model")
        self.assertTrue(resource_status(models["mut_model"])["available"])
        for name in ("ligandmpnn", "proteinmpnn", "solublempnn"):
            self.assertEqual(Path(models[name]["env_vars"]["LIGANDMPNN_DIR"]),
                             self.vendor / "ligandmpnn", name)

    def test_skill_environment_injects_bundled_paths(self):
        from enzyme_core.support.config import resource_environment_for_skill
        environment = resource_environment_for_skill(self.config, "ligandmpnn")
        self.assertEqual(Path(environment["LIGANDMPNN_DIR"]), self.vendor / "ligandmpnn")
        environment = resource_environment_for_skill(self.config, "mut-model-supervised-finetuning")
        self.assertEqual(Path(environment["MUT_MODEL_HOME"]), self.vendor / "mut_model")

    def test_only_expected_third_party_source_is_bundled(self):
        self.assertFalse((self.vendor / "VenusFSFP").exists())
        self.assertFalse((self.vendor / "gemme").exists())
