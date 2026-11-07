"""Real parsing facts for structure input completion, not MD science validation."""
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from enzyme_core.skills.registry import resolve_skill_file

class StructureInputs(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('structure_inputs',resolve_skill_file('public-data-inputs','scripts/inputs.py'))
        self.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.structure=self.root/'actual.pdb'
        self.structure.write_text('ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 10.00           C  \nATOM      2  CA  GLY A   2       2.000   3.000   4.000  1.00 10.00           C  \nEND\n')
    def inspect(self,sequence):
        fasta=self.root/'target.fasta';fasta.write_text('>target\n'+sequence+'\n')
        args=argparse.Namespace(structure=str(self.structure),sequence=str(fasta),output=str(self.root/'inspection.json'))
        with patch('pathlib.Path.cwd',return_value=self.root):self.module.inspect(args)
        return json.loads((self.root/'inspection.json').read_text())
    def test_exact_sequence_match_is_a_fact_not_applicability_proof(self):
        result=self.inspect('AG');self.assertTrue(result['chains'][0]['target_exact_match']);self.assertEqual(result['chains'][0]['residue_count'],2)
        self.assertIn('MD applicability',' '.join(result['limitations']))
    def test_mismatch_and_truncation_are_not_silently_equated(self):
        result=self.inspect('AGS');self.assertFalse(result['chains'][0]['target_exact_match']);self.assertEqual(result['chains'][0]['target_length'],3)
        self.assertTrue(result['chains'][0]['alignment'])
    def test_multiple_target_records_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'SINGLE_TARGET'):self.inspect('AG\n>another\nAGS')
    def test_empty_structure_is_rejected(self):
        self.structure.write_text('END\n')
        with self.assertRaises(ValueError):self.inspect('AG')

if __name__=='__main__': unittest.main()
