from .common import *
from fsys.domains.lean import fixture
class TestLeanRegression(unittest.TestCase):
    def test_real_lean_frozen_expansions(self):
        e=lean_engine();expected=(6,5,4,7,3,3)
        for i,(label,_,text,validations,heldout) in enumerate(fixture()['tasks']):
            r=e.learn(text,validations=validations)
            self.assertEqual(r.status,'FORMED',label);self.assertEqual(r.metrics['depth']['expansions'],expected[i]);self.assertEqual(r.verification.exit_code,0)
            if label=='C4':self.assertEqual(r.level,3)
            reuse=e.solve(heldout);self.assertEqual(reuse.status,'FULL_HIT');self.assertEqual(reuse.verification.exit_code,0)

