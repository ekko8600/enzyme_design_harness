import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from enzyme_core.skills.registry import resolve_skill_file


class PreparationTests(unittest.TestCase):
    def setUp(self):
        path=resolve_skill_file('public-data-inputs','scripts/prepare_structure.py')
        spec=importlib.util.spec_from_file_location('local_preparation',path)
        self.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.pdb=self.root/'assembly.pdb';lines=[];serial=0
        for chain in 'AB':
            for index,res in enumerate(['ALA','CYS','ASP'],1):
                serial+=1
                lines.append(f'ATOM  {serial:5d}  CA  {res} {chain}{index:4d}    {float(serial):8.3f}{0.:8.3f}{0.:8.3f}  1.00 20.00           C  \n')
            lines.append('TER\n')
        self.pdb.write_text(''.join(lines)+'END\n')
        self.fasta=self.root/'assay.fasta';self.fasta.write_text('>assay\nMACDK\n')

    def args(self,action,**kwargs):
        return argparse.Namespace(structure=str(self.pdb),action=action,model_index=None,chains=None,
            sequence=str(self.fasta),constraints=None,output=str(self.root/'output.json'),manifest=str(self.root/'manifest.json'),**kwargs)

    def test_full_chain_specific_mapping(self):
        self.module.prepare(self.args('map_residues'))
        rows=json.loads((self.root/'output.json').read_text())
        self.assertEqual(len(rows),6);self.assertEqual(rows[3]['chain'],'B')
        self.assertEqual(rows[3]['assay_position'],2);self.assertEqual(rows[3]['pose_position'],4)

    def test_ambiguous_or_missing_sequence_blocks(self):
        for seq in ['ACDACD','AD']:
            self.fasta.write_text('>assay\n'+seq+'\n')
            with self.assertRaisesRegex(ValueError,'AMBIGUOUS_OR_INCOMPLETE'):self.module.prepare(self.args('map_residues'))

    def test_unknown_chain_and_model_block(self):
        a=self.args('select_model');a.chains='C'
        with self.assertRaisesRegex(ValueError,'CHAIN_SELECTION'):self.module.prepare(a)
        a=self.args('select_model');a.model_index=0
        with self.assertRaisesRegex(ValueError,'MODEL_SELECTION'):self.module.prepare(a)

    def test_constraint_semantics_are_explicit(self):
        p=self.root/'constraints.json';p.write_text('{}');a=self.args('check_constraints');a.constraints=str(p)
        with self.assertRaisesRegex(ValueError,'CONSTRAINT_SEMANTICS'):self.module.prepare(a)
        p.write_text(json.dumps({'no_mutation':[{'chain':'B','residue_number':2}], 'no_repacking':[], 'no_movement':[]}))
        self.assertEqual(self.module.prepare(a)['chemical_identity_validation'],'NOT_INFERRED')

    def test_structure_selection_and_conversion(self):
        a=self.args('convert_structure');a.output=str(self.root/'converted.cif');a.chains='A'
        r=self.module.prepare(a);self.assertEqual(set(r['chains']),{'A'})
        from Bio.PDB import MMCIFParser
        structure=MMCIFParser(QUIET=True).get_structure('out',a.output)
        self.assertEqual(len(list(structure.get_atoms())),3)

    def test_conversion_preserves_nonprotein_chain(self):
        original=self.pdb.read_text().replace('END\n','')
        self.pdb.write_text(original+'HETATM    7  O1  LIG L   1       7.000   1.000   0.000  1.00 20.00           O  \nEND\n')
        a=self.args('convert_structure');a.output=str(self.root/'complex.cif')
        result=self.module.prepare(a)
        from Bio.PDB import MMCIFParser
        structure=MMCIFParser(QUIET=True).get_structure('out',a.output)
        self.assertEqual(len(list(structure.get_atoms())),7)
        self.assertEqual(result['non_protein_residues'][0]['chain'],'L')


if __name__=='__main__':unittest.main()
