from .common import *
class TestEngine(unittest.TestCase):
    def test_solve_does_not_learn(self):
        e=numeric_engine();before=e.library.persistent_hash;r=e.solve(NumericProblem(2,5,((3,7),)))
        self.assertEqual(r.status,'COMPLETION_VERIFIED');self.assertEqual(before,e.library.persistent_hash)
    def test_swap_components_identity(self):
        e=lean_engine();before=e.library.persistent_hash
        for strategy in (fs.SymbolicRetrieval(),fs.HybridRetrieval(),fs.AdaptiveRetrieval()):
            e.retrieval=strategy;e.planner=fs.DepthPlanner(indexed=False);r=e.solve(X);self.assertEqual(r.status,'FULL_HIT')
        self.assertEqual(before,e.library.persistent_hash)
        e.alpha_solver=fs.ContinuousAdamSolver(e.domain)
        e.set_backend(fs.CUDABackend());e.verifier=fs.NoOpVerifier()
        self.assertEqual(e.solve(X).status,'FULL_HIT');self.assertEqual(before,e.library.persistent_hash)
    def test_empty_library_foundation(self):
        d=NumericDomain();lib=fs.Library();e=fs.Engine(d,lib)
        self.assertIs(e.library,lib);self.assertEqual(len(lib),2)
