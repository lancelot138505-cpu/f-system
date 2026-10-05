from .common import *
from unittest.mock import patch
class TestCUDA(unittest.TestCase):
    def test_fallback(self):
        with patch.object(fs.CUDABackend,'available',return_value=False):
            self.assertEqual(fs.CUDABackend().alpha_backend().device,'cpu')
            e=lean_engine(fs.HybridRetrieval(),fs.BackendConfig(alpha_width='cuda'));self.assertEqual(e.solve(X).status,'FULL_HIT')
    @unittest.skipUnless(fs.CUDABackend.available(),'CUDA optional')
    def test_one_input_one_topk_output(self):
        e=lean_engine();cs=e.domain.retrieval_candidates(e.library);batch=e.domain.prepare_numeric_batch(cs,parse(X))
        r=fs.CUDABackend().alpha_backend().compute(batch,fs.OptimizationConfig(),2)
        self.assertEqual(r.diagnostics['h2d_transfer_calls'],1);self.assertEqual(r.diagnostics['d2h_transfer_calls'],1)
        self.assertEqual(r.diagnostics['explicit_synchronizations'],1)
        self.assertEqual(r.diagnostics['d2h_bytes'],2*(2+batch.shape[1]*batch.shape[2])*4)

