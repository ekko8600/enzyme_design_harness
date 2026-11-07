from enzyme_core.skills.registry import resolve_skill_file
"""Registered analysis regression using an explicitly synthetic three-frame fixture."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


class ProteinAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import mdtraj
            import numpy
            import pandas
            import matplotlib
            import PIL
        except ImportError as exc:
            raise unittest.SkipTest('Scientific environment required: ' + str(exc))
        path = resolve_skill_file('openmm-md','scripts/analyze_protein_trajectory.py')
        spec = importlib.util.spec_from_file_location('protein_analysis', path)
        cls.module = importlib.util.module_from_spec(spec); spec.loader.exec_module(cls.module)

    def fixture(self, root):
        import mdtraj as md
        import numpy as np
        top = md.Topology(); chain = top.add_chain()
        for i in range(3):
            r = top.add_residue('ALA', chain, resSeq=i+1)
            top.add_atom('CA', md.element.carbon, r)
        xyz = np.array([[[0,0,0],[.15,.05,0],[.3,0,.04]]], dtype=np.float32)
        xyz = np.concatenate([xyz + n*.1 for n in range(3)])
        traj = md.Trajectory(xyz, top, unitcell_lengths=np.ones((3,3))*3,
                             unitcell_angles=np.ones((3,3))*90)
        paths = {role:root/name for role,name in [('traj_dcd','mock.dcd'),('complex_pdb','mock.pdb'),('state_tsv','mock.tsv')]}
        traj.save_dcd(str(paths['traj_dcd'])); traj[0].save_pdb(str(paths['complex_pdb']))
        paths['state_tsv'].write_text('#"Step"\tPotential Energy (kJ/mole)\tTemperature (K)\n101000\t-100\t300\n102000\t-101\t301\n103000\t-102\t299\n')
        meta={'schema_version':'openmm-md-run/v2','args':{'equilibration_steps':100000},
              'derived':{'step_size_ps':.002,'trajectory_duration_ps':6},
              'artifacts':{k:{'sha256':self.module.sha(v)} for k,v in paths.items()}}
        manifest=root/'manifest.json'; manifest.write_text(json.dumps(meta))
        return paths,manifest,meta

    def test_step_time_and_real_input_hash_binding(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p,m,meta=self.fixture(root)
            with contextlib.redirect_stdout(io.StringIO()):
                result=self.module.analyze(p['traj_dcd'],p['complex_pdb'],m,p['state_tsv'],root/'analysis')
            self.assertEqual(result['frames'],3)
            self.assertAlmostEqual(result['first_saved_frame_ns'],.002)
            self.assertAlmostEqual(result['production_duration_ns'],.006)
            self.assertLess(result['rmsd_backbone_A']['max'],.01)
            for name in ['summary.json','timeseries.csv','rmsf.csv','md_overview.png','flexibility_calcium.png','structure_overlay.png','trajectory.gif']:
                self.assertGreater((root/'analysis'/name).stat().st_size,0)
            with p['traj_dcd'].open('ab') as f: f.write(b'tampered')
            with self.assertRaisesRegex(ValueError,'MD_INPUT_HASH_MISMATCH:traj_dcd'):
                self.module.analyze(p['traj_dcd'],p['complex_pdb'],m,p['state_tsv'],root/'bad')

    def test_duration_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p,m,meta=self.fixture(root)
            meta['derived']['trajectory_duration_ps']=1000;m.write_text(json.dumps(meta))
            with self.assertRaisesRegex(ValueError,'PRODUCTION_DURATION_NOT_CONFIRMED'):
                self.module.analyze(p['traj_dcd'],p['complex_pdb'],m,p['state_tsv'],root/'bad')

    def test_mass_weighted_rg_uses_center_of_mass(self):
        import numpy as np
        xyz=np.array([[[0.,0.,0.],[1.,0.,0.]]])
        self.assertAlmostEqual(self.module.mass_weighted_rg(xyz,[1.,3.])[0],np.sqrt(.1875))
        self.assertAlmostEqual(self.module.mass_weighted_rg(xyz+5,[1.,3.])[0],np.sqrt(.1875))
