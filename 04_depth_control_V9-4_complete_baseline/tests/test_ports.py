from .common import *
class TestPorts(unittest.TestCase):
    def test_lean_composition_verified_and_reloaded(self):
        e=lean_engine();Xv='P → P';context=e.context(Xv)
        s=e.library['f_imp_intro'].compose(e.library['f_exact'],(('state_out','state_in'),),context)
        artifact=e.execute(s,Xv);self.assertEqual(artifact.value,())
        receipt=e.verifier.verify(artifact,context);self.assertEqual(receipt.exit_code,0)
        stored=e.library.commit(s,receipt);self.assertTrue(e.domain.audit_structure(stored))
        self.assertEqual(e.execute(stored,'(A ∧ B) → (A ∧ B)').value,())
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            path=Path(td)/'composition.json';e.library.save(path);lib=fs.Library.load(path)
            loaded=fs.Engine(LeanPropDomain(),lib,verifier=e.verifier)
            self.assertEqual(loaded.execute(lib[s.identity],'Q → Q').value,())
    def test_closure(self):
        e=numeric_engine();a=e.library['f_double'];b=e.library['f_increment'];ctx=e.context(NumericProblem(2,5))
        c=e.formation.compose(a,b,(('value_out','value_in'),),ctx)
        self.assertIsInstance(c,fs.Composite);self.assertEqual(c.parents,(a.identity,b.identity))
        self.assertEqual(len(e.library),2)
    def test_bad_direction_and_cardinality(self):
        d=NumericDomain();a=fs.Port('a','output','float','value');b=fs.Port('b','input','string','value')
        self.assertFalse(d.port_compatible(a,b))
        with self.assertRaises(ValueError):fs.Port('x','invalid','float','value')
        with self.assertRaises(ValueError):fs.Port('x','input','float','value',0)
        e=numeric_engine()
        with self.assertRaises(ValueError):e.formation.compose(e.library['f_double'],e.library['f_increment'],(('value_in','value_out'),),e.context(NumericProblem(2,5)))
