import csv,hashlib,json
from pathlib import Path
import tempfile
import unittest
from enzyme_core.api import verify_operation_output
from enzyme_core.support.html_report_renderer import polish_existing_html


class OutputChecks(unittest.TestCase):
    def test_html_language_detection_explicit_and_preserved(self):
        self.assertIn('lang="zh-CN"',polish_existing_html('<p>执行摘要</p>'))
        self.assertIn('lang="en"',polish_existing_html('<p>Summary</p>'))
        self.assertIn('lang="fr"',polish_existing_html('<p>执行摘要</p>',lang='fr'))
        self.assertIn('lang="ja"',polish_existing_html('<html lang="ja"><body>科学</body></html>'))

    def test_language_mismatch_is_real_output_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'page.html';p.write_text('<html lang="en"><p>执行摘要</p></html>')
            args={'inputs':{},'outputs':{'page':str(p)},'parameters':{'body':'<p>执行摘要</p>'}}
            self.assertEqual(verify_operation_output('html-report-renderer','render_html',**args)['status'],'FAIL')
            p.write_text(polish_existing_html(args['parameters']['body']))
            self.assertEqual(verify_operation_output('html-report-renderer','render_html',**args)['status'],'PASS')

    def test_full_decomposition_detects_corruption_and_missing_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'models';source.mkdir();out=root/'out';out.mkdir()
            pdb=source/'m.pdb';pdb.write_text('ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00 20.00           N\n')
            data={'schema_version':'enzyme-core/energy-decomposition/v1','scorefxn':'ref2015','seed':1729,'terms':[{'term':'fa_atr','weight':1.0}], 'models':[{'model':'m.pdb','sha256':hashlib.sha256(pdb.read_bytes()).hexdigest(),'residues':1,'total_REU':-2.0,'global_term_sum_REU':-2.0,'residue_term_sum_REU':-2.0}]}
            (out/'manifest.json').write_text(json.dumps(data))
            fields=['model','scope','pose_position','chain','residue_number','insertion_code','resname','term','raw_energy','weight','weighted_energy','unit']
            rows=[['m.pdb','global',0,'','','','','fa_atr',-2.,1.,-2.,'REU'],['m.pdb','residue',1,'A',1,'','ALA','fa_atr',-2.,1.,-2.,'REU']]
            def run(values):
                with (out/'energy_terms.csv').open('w',newline='') as f:
                    writer=csv.writer(f);writer.writerow(fields);writer.writerows(values)
                return verify_operation_output('pyrosetta-scoring','decompose_structures',inputs={'input_dir':str(source)},outputs={'result':str(out)},parameters={'seed':1729,'scorefxn':'ref2015'})
            self.assertEqual(run(rows)['status'],'PASS')
            bad=[list(r) for r in rows];bad[-1][-2]=4.;self.assertEqual(run(bad)['status'],'FAIL')
            self.assertEqual(run(rows[:1])['status'],'FAIL')
            self.assertEqual(run(rows+rows[-1:])['status'],'FAIL')
            bad=[list(r) for r in rows];bad[-1][3]='B';self.assertEqual(run(bad)['status'],'FAIL')
            old_terms=data['terms'];data['terms']=[{'term':'t'+str(i),'weight':1.0} for i in range(1000)]
            (out/'manifest.json').write_text(json.dumps(data));result=run(rows)
            self.assertEqual(result['status'],'FAIL');self.assertIn('DECOMPOSITION_COUNT_TOO_LARGE',result['error'])
            data['terms']=old_terms;(out/'manifest.json').write_text(json.dumps(data))
            pdb.write_text(pdb.read_text()+'ATOM      2  N   GLY A   2       1.000   0.000   0.000  1.00 20.00           N\n')
            data['models'][0]['sha256']=hashlib.sha256(pdb.read_bytes()).hexdigest();(out/'manifest.json').write_text(json.dumps(data))
            result=run(rows);self.assertEqual(result['status'],'FAIL');self.assertIn('DECOMPOSITION_MISSING_PDB_RESIDUES',result['error'])
