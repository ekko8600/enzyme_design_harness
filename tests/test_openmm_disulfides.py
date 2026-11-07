from enzyme_core.skills.registry import resolve_skill_file
"""Scientific-environment regression; synthetic topology is not MD evidence."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


class DisulfidePreservation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(resolve_skill_file('openmm-md','scripts/run_openmm_md.py').parent))
        try:
            import simulate
        except ImportError as exc:
            raise unittest.SkipTest('OpenMM scientific environment required: ' + str(exc))
        cls.simulate = simulate

    def fixture(self):
        from openmm import Vec3, unit
        from openmm.app import Topology, element
        t = Topology(); c = t.addChain('A'); sulfurs = []
        for number in (34, 123, 99):
            r = t.addResidue('CYS', c, str(number))
            s = t.addAtom('SG', element.sulfur, r)
            h = t.addAtom('HG', element.hydrogen, r)
            t.addBond(s, h); sulfurs.append(s)
        t.addBond(sulfurs[0], sulfurs[1])
        return SimpleNamespace(topology=t, positions=[Vec3(i*.1,0,0) for i in range(6)]*unit.nanometer)

    def test_cystine_hg_removed_other_thiol_retained_and_bond_restored(self):
        f = self.fixture(); records = self.simulate._disulfide_records(f.topology)
        self.assertEqual(self.simulate._remove_disulfide_thiol_hydrogens(f, records), 2)
        self.assertEqual([a.residue.id for a in f.topology.atoms() if a.name == 'HG'], ['99'])
        f.topology._bonds = [(a,b) for a,b in f.topology.bonds() if not a.name == b.name == 'SG']
        self.simulate._restore_disulfides(f.topology, records)
        self.assertEqual(self.simulate._disulfide_records(f.topology), records)
        self.simulate._restore_disulfides(f.topology, records)
        self.assertEqual(len(self.simulate._disulfide_records(f.topology)), 1)

    def test_missing_sulfur_fails_closed(self):
        f = self.fixture(); records = self.simulate._disulfide_records(f.topology)
        from openmm.app import Modeller
        m = Modeller(f.topology, f.positions)
        m.delete([a for a in m.topology.atoms() if a.name == 'SG' and a.residue.id == '123'])
        with self.assertRaisesRegex(ValueError, 'DISULFIDE_SG_MISSING'):
            self.simulate._restore_disulfides(m.topology, records)
