from .common import *
class TestAlpha(unittest.TestCase):
    def test_dynamic_space(self):
        e=lean_engine();s=e.library['F_identity'];d=e.domain
        a=fs.DirectStructuralSolver(d).solve(s,parse('P → P'));b=fs.DirectStructuralSolver(d).solve(s,parse(X))
        self.assertNotEqual(a.shape,b.shape);self.assertEqual(d.decode_alpha(s,parse(X),b)['A'],parse('A ∧ B'))
        self.assertNotIn('candidate_count',vars(fs.ArchConfig()))
    def test_invalid(self):
        with self.assertRaises(ValueError):fs.AlphaTensor((1.,),(True,),(2,))
        with self.assertRaises(ValueError):fs.AlphaTensor((float('nan'),),(True,),(1,))
        with self.assertRaises(NotImplementedError):fs.OneShotAlphaPredictor().solve(None,None)
        self.assertEqual(fs.AlphaTensor([1],[True],[1]).shape,(1,))
    def test_batch_order(self):
        e=lean_engine();d=e.domain;ss=[e.library['F_and_swap'],e.library['F_identity']]
        out=fs.ContinuousAdamSolver(d).solve_batch(ss,parse(X))
        self.assertEqual([a.shape[0] for a in out],[len(s.alpha_schema.binding_slots) for s in ss])
        self.assertTrue(d.binding_legal(ss[1],parse(X),d.decode_alpha(ss[1],parse(X),out[1])))
