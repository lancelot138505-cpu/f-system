from .common import *
class TestCPU(unittest.TestCase):
    def test_numeric_only(self):
        e=lean_engine();cs=e.domain.retrieval_candidates(e.library);Xv=parse(X);batch=e.domain.prepare_numeric_batch(cs,Xv)
        result=fs.CPUBackend().alpha_backend().compute(batch,fs.OptimizationConfig(),2)
        self.assertEqual(len(result.indices),2);self.assertEqual(result.diagnostics['device'],'cpu')
        self.assertEqual(result.diagnostics['h2d_bytes'],0);self.assertFalse(result.diagnostics['default_returns_all_alpha'])
    def test_arch_separation(self):
        with self.assertRaises(ValueError):fs.BackendConfig(default='cuda')
        d=NumericDomain();e=fs.Engine(d,d.bootstrap(),backend=fs.CPUBackend());self.assertEqual(e.solve(NumericProblem(3,6)).status,'FULL_HIT')
