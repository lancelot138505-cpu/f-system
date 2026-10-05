from .common import *
from dataclasses import FrozenInstanceError
class TestProvenance(unittest.TestCase):
    def test_formation_separate_from_execution(self):
        e=lean_engine();before=e.library.persistent_hash;prov=e.library['F_identity'].formation_provenance.to_dict()
        a=e.solve(X);b=e.solve('P → P')
        self.assertEqual(a.status,'FULL_HIT');self.assertEqual(b.status,'FULL_HIT')
        self.assertEqual(prov,e.library['F_identity'].formation_provenance.to_dict());self.assertEqual(before,e.library.persistent_hash)
        self.assertNotEqual(a.context.X,b.context.X)
        with self.assertRaises(FrozenInstanceError):e.library['F_identity'].formation_provenance.source='changed'

