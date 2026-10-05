from .common import *
class TestSymbolic(unittest.TestCase):
    def test_no_adam(self):
        e=lean_engine();ctx=e.context(X);r=fs.SymbolicRetrieval().retrieve(ctx)
        self.assertEqual(r.status,'FULL_HIT');self.assertEqual(r.metrics['alpha_evaluations'],0);self.assertIsNone(e._alpha_backend)
    def test_true_miss_audit(self):
        e=lean_engine();ctx=e.context('P → Q → R → (P ∧ Q) ∧ R');c=e.domain.retrieval_candidates(e.library)
        r=fs.audit_retrieval(e.domain,c,ctx.X,[s.identity for s in c],[],context=ctx)
        self.assertEqual(r['classification'],'TRUE_LIBRARY_MISS');self.assertFalse(r['audit_used_for_online_selection'])

