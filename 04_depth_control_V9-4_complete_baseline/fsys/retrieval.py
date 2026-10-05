from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import time
from .alpha import ContinuousAdamSolver

@dataclass
class RetrievalResult:
    status: str
    selected: object = None
    binding: dict = field(default_factory=dict)
    alternatives: tuple = ()
    anchors: tuple = ()
    metrics: dict = field(default_factory=dict)

class RetrievalStrategy(ABC):
    @abstractmethod
    def retrieve(self, context): ...

def choose_valid(context,rows,metrics):
    rows.sort(key=lambda r:(-r[0].level,1,-r[2],r[0].identity))
    return RetrievalResult("FULL_HIT" if rows else "TRUE_LIBRARY_MISS",
                rows[0][0] if rows else None,rows[0][1] if rows else {},
                tuple(s.identity for s,_,_ in rows),metrics=metrics)

def _candidate_pool(context):
    candidates=context.domain.retrieval_candidates(context.library)
    scope=context.retrieval_scope
    return candidates if scope is None else [s for s in candidates if s.identity in scope]

class SymbolicRetrieval(RetrievalStrategy):
    def retrieve(self,context):
        st=time.perf_counter();domain=context.domain
        candidates=_candidate_pool(context);eligible=domain.prefilter(candidates,context.X)
        rows=[]
        for s in eligible:
            context.count("structural_checks");m=domain.structural_exact(s,context.X,context)
            if m.exact:rows.append((s,m.binding,1.))
        result=choose_valid(context,rows,{"method":"symbolic","before":len(candidates),"after":len(eligible),
                                          "alpha_evaluations":0,"wall_seconds":time.perf_counter()-st})
        if not rows:result.anchors=tuple(domain.structural_partial(s,context.X,context) for s in candidates)
        return result

class ContinuousRetrieval(RetrievalStrategy):
    prefilter_enabled=False
    def __init__(self,top_k=5,optimization=None):
        from .config import OptimizationConfig
        self.top_k,self.optimization=top_k,optimization or OptimizationConfig()
    def retrieve(self,context):
        st=time.perf_counter();domain=context.domain;candidates=_candidate_pool(context)
        eligible=domain.prefilter(candidates,context.X) if self.prefilter_enabled else candidates
        if not eligible:return RetrievalResult("TRUE_LIBRARY_MISS",metrics={"method":self.__class__.__name__,"alpha_evaluations":0,"wall_seconds":time.perf_counter()-st})
        batch=domain.prepare_numeric_batch(eligible,context.X,context)
        output=context.engine.alpha_backend.compute(batch,self.optimization,self.top_k)
        rows=[];anchors=[];decode_time=check_time=0.
        for i,score,values in zip(output.indices,output.scores,output.alphas):
            s=eligible[int(i)];t=time.perf_counter();alpha=domain.alpha_from_batch(s,context.X,values,batch)
            binding=domain.decode_alpha(s,context.X,alpha);decode_time+=time.perf_counter()-t
            t=time.perf_counter();context.count("structural_checks");m=domain.structural_exact(s,context.X,context);check_time+=time.perf_counter()-t
            context.trace.append({"phase":"width","structure_id":s.identity,"score":float(score),"decoded_legal":domain.binding_legal(s,context.X,binding),"alpha_solver":"continuous_adam"})
            if m.exact and domain.binding_legal(s,context.X,binding):rows.append((s,binding,float(score)))
            anchors.append(domain.structural_partial(s,context.X,context,score=float(score)))
        metrics={"method":self.__class__.__name__,"before":len(candidates),"after":len(eligible),"alpha_evaluations":len(eligible),
                 "decode_cpu_seconds":decode_time,"checker_cpu_seconds":check_time,**output.timing,**output.diagnostics,
                 "wall_seconds":time.perf_counter()-st}
        result=choose_valid(context,rows,metrics);result.anchors=tuple(anchors)
        if not rows:result.status="NO_TOPK_EXACT"
        return result

class HybridRetrieval(ContinuousRetrieval):prefilter_enabled=True

class AdaptiveRetrieval(RetrievalStrategy):
    def __init__(self,top_k=5):self.top_k=top_k
    def retrieve(self,context):
        from .backend import CUDABackend
        domain=context.domain;candidates=_candidate_pool(context);eligible=domain.prefilter(candidates,context.X)
        features={"library_size":len(candidates),"prefilter_count":len(eligible),"retention_ratio":len(eligible)/max(1,len(candidates)),
                  "candidate_complexity":sum(domain.structural_signature(s).get("node_count",1) for s in eligible)/max(1,len(eligible)),
                  "K_X":domain.candidate_count(context.X),"gpu_available":CUDABackend.available() and context.engine.backend_config.alpha_width!="cpu"}
        decision=context.engine.cost_policy.decide(features);context.trace.append({"phase":"cost_policy",**decision})
        strategies={"symbolic":SymbolicRetrieval(),"continuous":ContinuousRetrieval(self.top_k),"hybrid":HybridRetrieval(self.top_k)}
        result=strategies[decision["chosen"]].retrieve(context)
        if result.status=="NO_TOPK_EXACT":
            context.trace.append({"phase":"adaptive_correctness_fallback","from":decision["chosen"],"to":"symbolic"})
            previous=result.metrics;result=SymbolicRetrieval().retrieve(context);result.metrics["previous_strategy"]=previous
        context.engine.cost_policy.observe(decision['chosen'],result.metrics,features)
        result.metrics["cost_decision"]=decision;return result
