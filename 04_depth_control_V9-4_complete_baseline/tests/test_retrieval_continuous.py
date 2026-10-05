from .common import *
from unittest.mock import patch
class TestContinuous(unittest.TestCase):
    def test_rank_and_check(self):
        e=lean_engine(fs.ContinuousRetrieval());r=e.solve(X)
        self.assertEqual(r.status,'FULL_HIT');self.assertGreater(r.metrics['retrieval']['alpha_evaluations'],0)
        self.assertTrue(any(t.get('decoded_legal') for t in r.context.trace))
    def test_bad_decoded_alpha_not_repaired(self):
        e=lean_engine(fs.ContinuousRetrieval());context=e.context(X)
        with patch.object(e.domain,'binding_legal',return_value=False):
            r=e.retrieval.retrieve(context)
        self.assertIsNone(r.selected);self.assertEqual(r.status,'NO_TOPK_EXACT')
