from .common import *
class TestFormation(unittest.TestCase):
    def test_verified_gate(self):
        e=numeric_engine();before=e.library.persistent_hash
        e.verifier=fs.RuleVerifier(lambda a:False);r=e.learn(NumericProblem(2,5,((3,7),)))
        self.assertEqual(r.status,'VERIFY_FAIL');self.assertEqual(before,e.library.persistent_hash)
    def test_hierarchy(self):
        e=numeric_engine();r=e.learn(NumericProblem(2,5,((3,7),)));self.assertEqual(r.status,'FORMED')
        self.assertEqual(e.library.primitives(r.structure_id),('f_double','f_increment'));self.assertEqual(r.level,1)

