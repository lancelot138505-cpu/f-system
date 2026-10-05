from enum import Enum
class FailureType(str,Enum):
    PREFILTER_FALSE_NEGATIVE='PREFILTER_FALSE_NEGATIVE'
    ALPHA_RANKING_MISS='ALPHA_RANKING_MISS'
    STRUCTURAL_EXACT_REJECT='STRUCTURAL_EXACT_REJECT'
    TRUE_LIBRARY_MISS='TRUE_LIBRARY_MISS'
    PARTIAL_ANCHOR_MISS='PARTIAL_ANCHOR_MISS'
    SELECTOR_BUDGET_EXHAUSTED='SELECTOR_BUDGET_EXHAUSTED'
    SELECTOR_NO_PATH='SELECTOR_NO_PATH'
    FORMATION_REJECTED='FORMATION_REJECTED'
    VERIFY_FAIL='VERIFY_FAIL'

def audit_retrieval(domain,candidates,X,eligible_ids,ranked_ids,top_k=5,context=None):
    """Offline classification only; never supplies oracle IDs to a runtime ranker."""
    valid=[s.identity for s in candidates if domain.structural_exact(s,X,context).exact]
    fn=sorted(set(valid)-set(eligible_ids));hit=bool(set(valid)&set(ranked_ids[:top_k]))
    status='PREFILTER_FALSE_NEGATIVE' if fn else 'FULL_HIT' if hit else 'ALPHA_RANKING_MISS' if valid else 'TRUE_LIBRARY_MISS'
    return {'classification':status,'exact_ids':valid,'prefilter_false_negatives':fn,'audit_used_for_online_selection':False}
