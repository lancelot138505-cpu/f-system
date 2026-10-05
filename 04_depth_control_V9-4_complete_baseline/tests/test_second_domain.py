from .common import *
from tempfile import TemporaryDirectory
class TestSecondDomain(unittest.TestCase):
    def test_shared_autonomous_lifecycle(self):
        e=numeric_engine();r=e.learn(NumericProblem(2,5,((3,7),(-1,-1))),validations=(NumericProblem(4,9),))
        self.assertEqual(r.status,'FORMED');self.assertIsInstance(e.formation,fs.FormationEngine);self.assertIsInstance(e.library[r.structure_id],fs.Composite)
        self.assertEqual(e.solve(NumericProblem(10,21)).status,'FULL_HIT')
        with TemporaryDirectory() as td:
            path=Path(td)/'numeric.json';e.library.save(path);loaded=fs.Library.load(path)
            self.assertEqual(fs.Engine(NumericDomain(),loaded).solve(NumericProblem(-3,-5)).status,'FULL_HIT')
    def test_za_capacity_and_freeze(self):
        z=fs.ZAKernel(1).fit([-1,0,1],[-2,0,2]);self.assertAlmostEqual(z.formation_parameters()['A'][0][0],2)
        with self.assertRaises(RuntimeError):z.fit([1],[999])
        multi=fs.ZAKernel(2).fit([[1,0],[0,1],[1,1]],[2,3,5]);A=multi.formation_parameters()['A']
        self.assertAlmostEqual(A[0][0],2);self.assertAlmostEqual(A[1][0],3)
