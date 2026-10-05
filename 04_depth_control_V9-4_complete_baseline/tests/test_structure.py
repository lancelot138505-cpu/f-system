from .common import *
from dataclasses import fields,FrozenInstanceError
class TestStructure(unittest.TestCase):
    def test_unified_fields(self):
        e=numeric_engine();leaf=e.library['f_double'];r=e.learn(NumericProblem(2,5,((3,7),)));composite=e.library[r.structure_id]
        self.assertIsInstance(leaf,fs.Structure);self.assertIsInstance(composite,fs.Structure)
        self.assertEqual([f.name for f in fields(leaf)],[f.name for f in fields(composite)])
        for s in (leaf,composite):self.assertEqual(fs.Structure.from_dict(s.to_dict()).persistent_hash,s.persistent_hash)
        self.assertAlmostEqual(e.execute(leaf,NumericProblem(4,8)).value,8)
        self.assertAlmostEqual(e.execute(composite,NumericProblem(4,9)).value,9)
        lean=lean_engine();transition=lean.execute(lean.library['f_imp_intro'],'P → P')
        self.assertEqual(transition.value,(((parse('P'),),parse('P')),))
    def test_immutable_and_runtime_free(self):
        s=numeric_engine().library['f_double']
        with self.assertRaises(FrozenInstanceError):s.level=9
        with self.assertRaises(TypeError):s.metadata.foo='x'
        with self.assertRaises(ValueError):fs.Leaf('bad','op',metadata={'X':1})
        with self.assertRaises(TypeError):fs.Leaf('bad','op',metadata={'payload':object()})
        with self.assertRaises(ValueError):fs.Composite('bad','op')
