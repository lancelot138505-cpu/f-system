from .common import *
class TestHybrid(unittest.TestCase):
    def test_prefilter(self):
        e=lean_engine(fs.HybridRetrieval());r=e.solve(X);self.assertEqual(r.status,'FULL_HIT')
        self.assertLess(r.metrics['retrieval']['after'],r.metrics['retrieval']['before'])
    def test_adaptive_log(self):
        e=lean_engine(fs.AdaptiveRetrieval());r=e.solve(X)
        self.assertEqual(r.status,'FULL_HIT');self.assertEqual(r.metrics['retrieval']['cost_decision']['chosen'],'symbolic')

