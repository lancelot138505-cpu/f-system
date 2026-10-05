from .common import *
class TestDomain(unittest.TestCase):
    def test_boundary(self):
        d=LeanPropDomain()
        with self.assertRaises(ValueError):d.validate_instance(12)
        with self.assertRaises(ValueError):d.validate_instance('P + Q')
        self.assertIsNotNone(d.countermodel(parse('P → Q')))
        self.assertIsNone(d.countermodel(parse('P → P')))
    def test_index_legality_superset(self):
        e=lean_engine();states=[(((),parse(X)),),(((parse('P'),parse('P → Q')),parse('Q')),),(((parse('P ∧ Q'),),parse('P')),)]
        for state in states:
            ctx=e.context(X);ctx.index_enabled=False;allactions=e.domain.search_actions(state,ctx)
            ctx=e.context(X);ctx.index_enabled=True;indexed=e.domain.search_actions(state,ctx)
            self.assertEqual({repr(a) for a in allactions},{repr(a) for a in indexed})

