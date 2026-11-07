from enzyme_core.skills.registry import resolve_skill_file
"""Synthetic checkpoint regression; does not run a scientific model."""
import argparse
import importlib.util
import io
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class EsmCheckpointSafetyTests(unittest.TestCase):
    def test_namespace_is_scoped_to_the_offline_load(self):
        try:
            import torch
        except ImportError:
            self.skipTest('torch is unavailable')
        if not hasattr(torch.serialization, 'safe_globals'):
            self.skipTest('safe_globals is unavailable')
        source = resolve_skill_file('esm','scripts/local_esm2_zeroshot.py')
        spec = importlib.util.spec_from_file_location('esm_safety_fixture', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        stream = io.BytesIO()
        torch.save({'args': argparse.Namespace(layers=2)}, stream)
        def load(_path):
            stream.seek(0)
            return torch.load(stream)
        esm = types.SimpleNamespace(pretrained=types.SimpleNamespace(fake_model=lambda: None,
                                      load_model_and_alphabet_local=load))
        before = list(torch.serialization.get_safe_globals())
        with patch.dict(sys.modules, {'esm': esm}), patch.dict('os.environ',{'TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD':'1'}), patch('enzyme_core.support.offline_models.model_file', return_value=Path('/fixture.pt')):
            value = module.load_esm2('fake_model', offline=True)
        self.assertEqual(value['args'].layers, 2)
        # PyTorch may reorder this process-global allowlist when the scoped
        # safe_globals context exits; only membership must be restored.
        self.assertCountEqual(torch.serialization.get_safe_globals(), before)

    def test_esmif_load_is_scoped_and_forces_weights_only(self):
        try:
            import torch
        except ImportError:
            self.skipTest('torch is unavailable')
        if not hasattr(torch.serialization, 'safe_globals'):
            self.skipTest('safe_globals is unavailable')
        source = resolve_skill_file('esmif','scripts/local_esmif_zeroshot.py')
        spec = importlib.util.spec_from_file_location('esmif_safety_fixture', source)
        module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        stream = io.BytesIO();torch.save({'args':argparse.Namespace(layers=2)},stream)
        def load(_path):
            stream.seek(0)
            return torch.load(stream)
        esm=types.SimpleNamespace(pretrained=types.SimpleNamespace(load_model_and_alphabet_local=load))
        before=list(torch.serialization.get_safe_globals()); original=torch.load
        with patch.dict('os.environ',{'TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD':'1'}):
            value=module.load_esmif(esm,Path('/fixture.pt'))
        self.assertEqual(value['args'].layers,2)
        self.assertIs(torch.load,original)
        self.assertCountEqual(torch.serialization.get_safe_globals(),before)

    def test_esmif_loader_rejects_unlisted_pickle_globals(self):
        import os,tempfile
        try:
            import torch
        except ImportError:
            self.skipTest('torch is unavailable')
        if not hasattr(torch.serialization, 'safe_globals'):
            self.skipTest('safe_globals is unavailable')
        source = resolve_skill_file('esmif','scripts/local_esmif_zeroshot.py')
        spec = importlib.util.spec_from_file_location('esmif_safety_reject', source)
        module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            marker=Path(folder)/'executed'
            class Unsafe:
                def __reduce__(self):return (os.system,('touch '+str(marker),))
            stream=io.BytesIO();torch.save({'payload':Unsafe()},stream)
            def load(_path):
                stream.seek(0)
                return torch.load(stream)
            esm=types.SimpleNamespace(pretrained=types.SimpleNamespace(load_model_and_alphabet_local=load))
            with self.assertRaises(Exception):module.load_esmif(esm,Path('/fixture.pt'))
            self.assertFalse(marker.exists())

    def test_hub_loading_for_both_esm_backends_forces_weights_only(self):
        try:
            import torch
        except ImportError:
            self.skipTest('torch is unavailable')
        if not hasattr(torch.serialization, 'safe_globals'):
            self.skipTest('safe_globals is unavailable')
        seen=[]
        def fake_hub(*args,**kwargs):seen.append(kwargs);return {'model':'fixture'}
        for skill,script,function in [('esm','scripts/local_esm2_zeroshot.py','esm2'),('esmif','scripts/local_esmif_zeroshot.py','esmif')]:
            source=resolve_skill_file(skill,script);spec=importlib.util.spec_from_file_location('safe_hub_'+skill,source)
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            fake=types.SimpleNamespace(pretrained=types.SimpleNamespace(
                fake_model=lambda:torch.hub.load_state_dict_from_url('https://fixture.invalid/weights'),
                esm_if1_gvp4_t16_142M_UR50=lambda:torch.hub.load_state_dict_from_url('https://fixture.invalid/weights')))
            with patch.object(torch.hub,'load_state_dict_from_url',fake_hub),patch.dict(sys.modules,{'esm':fake}):
                result=module.load_esm2('fake_model') if function=='esm2' else module.load_esmif(fake)
                self.assertIs(torch.hub.load_state_dict_from_url,fake_hub)
            self.assertEqual(result,{'model':'fixture'})
        self.assertEqual([r['weights_only'] for r in seen],[True,True])


if __name__ == '__main__':
    unittest.main()
