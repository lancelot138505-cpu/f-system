from dataclasses import dataclass, field
import time
from .provenance import ExecutionProvenance

@dataclass
class ExecutionContext:
    X: object
    domain: object
    library: object
    engine: object = None
    alpha: object = None
    candidate_map: tuple = ()
    decoded_binding: dict = field(default_factory=dict)
    temporary_substitution: dict = field(default_factory=dict)
    current_state: object = None
    trace: list = field(default_factory=list)
    timing: dict = field(default_factory=dict)
    backend_info: dict = field(default_factory=dict)
    counters: dict = field(default_factory=dict)
    compatibility_cache: dict = field(default_factory=dict)
    validations: tuple = ()
    cache_enabled: bool = True
    index_enabled: bool = True
    traversed_structures: list = field(default_factory=list)
    operation_edge_observer: object = None
    retrieval_scope: frozenset | None = None
    search_scope: frozenset | None = None
    def count(self,key,n=1):self.counters[key]=self.counters.get(key,0)+n

@dataclass
class SolveResult:
    status: str
    value: object = None
    structure_id: str | None = None
    parents: tuple = ()
    level: int | None = None
    persistent_hash: str | None = None
    verification: object = None
    context: ExecutionContext | None = None
    metrics: dict = field(default_factory=dict)

class Engine:
    def __init__(self,domain,library=None,arch=None,retrieval=None,backend=None,verifier=None,
                 planner=None,cost_policy=None,alpha_solver=None,usage_registry=None):
        from .library import Library
        from .config import ArchConfig,BackendConfig
        from .retrieval import SymbolicRetrieval
        from .search import DepthPlanner
        from .backend import Backend,CPUBackend,CUDABackend
        from .cost import CostPolicy
        from .formation import FormationEngine
        from .index import StructureIndex
        from .preference import UsageRegistry
        from .operation_pool import OperationPoolProvider
        self.domain=domain;self.library=library if library is not None else Library()
        self.arch=arch or ArchConfig();self.retrieval=retrieval or SymbolicRetrieval()
        supplied_backend=backend if isinstance(backend,Backend) else None
        self.backend_config=BackendConfig(alpha_width='cuda' if isinstance(supplied_backend,CUDABackend) else 'cpu') if supplied_backend else backend or BackendConfig()
        self.planner=planner or DepthPlanner()
        self.cost_policy=cost_policy or CostPolicy();self.verifier=verifier or domain.default_verifier()
        from .alpha import DirectStructuralSolver
        self.alpha_solver=alpha_solver or DirectStructuralSolver(domain)
        # The domain supplies foundational knowledge through the same verified gate.
        if len(self.library)==0 and hasattr(domain,'bootstrap'):
            from .verifier import VerificationRecord
            foundation=domain.bootstrap(self.verifier) if getattr(domain,'bootstrap_accepts_verifier',False) else domain.bootstrap()
            for s in foundation:self.library.commit(s,VerificationRecord(True,s.formation_provenance.verifier,s.formation_provenance.verification_digest))
        self.backend=supplied_backend or (CUDABackend() if self.backend_config.alpha_width in ("cuda","auto") else CPUBackend())
        self._alpha_backend=None;self.index=StructureIndex(self.library,domain);self.formation=FormationEngine(self)
        self.usage=usage_registry or UsageRegistry(self.library)
        if self.usage.library is not self.library:raise ValueError("usage registry must belong to the Engine Library")
        self.operation_pool=OperationPoolProvider(self.library,self.usage)
        if not all(domain.audit_structure(s) for s in self.library):raise ValueError("domain persistent audit failed")
        if any(s.level>self.arch.max_level or len(s.alpha_schema.binding_slots)>self.arch.slot_capacity for s in self.library):raise ValueError('library exceeds architecture capacities')
    @property
    def alpha_backend(self):
        if self._alpha_backend is None:self._alpha_backend=self.backend.alpha_backend()
        return self._alpha_backend
    def context(self,X,validations=()):
        X=self.domain.validate_instance(X)
        return ExecutionContext(X,self.domain,self.library,self,candidate_map=tuple(self.domain.candidate_space(X)),validations=tuple(validations),
                                backend_info={'structural':'cpu','alpha_width_requested':self.backend_config.alpha_width})
    def search_exposure(self,lifecycle_view='ACTIVE',structure_ids=None):
        """Structures visible to framework search after lifecycle and domain audit."""
        return self.operation_pool.get_search_structures(self.domain,
            lifecycle_view=lifecycle_view,structure_ids=structure_ids)
    def retrieval_search_exposure(self,lifecycle_view='ACTIVE'):
        """Lifecycle-filtered subset used by Engine.solve's ordinary retrieval."""
        eligible_ids=tuple(s.identity for s in self.domain.retrieval_candidates(self.library))
        return self.operation_pool.get_search_structures(self.domain,
            lifecycle_view=lifecycle_view,structure_ids=eligible_ids)
    def _reuse(self,context,retrieval):
        before=self.library.persistent_hash;s=retrieval.selected
        context.traversed_structures.clear();context.current_state=None;context.temporary_substitution={}
        try:
            st=time.perf_counter();context.alpha=self.alpha_solver.solve(s,context.X,context)
            if context.alpha is None:return SolveResult("STRUCTURAL_EXACT_REJECT",context=context)
            context.decoded_binding=self.domain.decode_alpha(s,context.X,context.alpha)
            if not self.domain.binding_legal(s,context.X,context.decoded_binding):return SolveResult("STRUCTURAL_EXACT_REJECT",context=context)
            context.temporary_substitution=dict(retrieval.binding)
            artifact=s.execute(context.X,context.alpha,context);execution_seconds=time.perf_counter()-st
            verification=self.verifier.verify(artifact,context)
        except (ValueError,KeyError,TypeError) as error:
            if before!=self.library.persistent_hash:raise RuntimeError("failed reuse mutated persistent library")
            context.trace.append({"phase":"preference_candidate_rejected","structure_id":s.identity,
                                  "status":"STRUCTURAL_EXACT_REJECT","reason":str(error)})
            return SolveResult("STRUCTURAL_EXACT_REJECT",context=context,metrics={"reason":str(error)})
        if before!=self.library.persistent_hash:raise RuntimeError("reuse mutated persistent library")
        status="FULL_HIT" if verification.passed else "VERIFY_FAIL"
        context.trace.append(ExecutionProvenance(s.identity,'cpu',status).__dict__)
        if verification.passed:
            usage_event=self.usage.record_successful_path(context.traversed_structures,verification)
            context.trace.append({"phase":"usage_update",**usage_event})
            retrieval.metrics["active_switches"]=usage_event["active_switches"]
        context.timing.update(execution_seconds=execution_seconds,verification_seconds=verification.elapsed_seconds)
        return SolveResult(status,artifact.value,s.identity,s.parents,s.level,s.persistent_hash,verification,context,
                           {"retrieval":retrieval.metrics,"execution_seconds":execution_seconds,"verification_seconds":verification.elapsed_seconds})
    def _verify_plan(self,plan,context):
        from .verifier import VerificationRecord
        try:
            artifact=self.domain.artifact_from_plan(plan,context)
            verification=self.verifier.verify(artifact,context)
            return artifact,verification
        except (ValueError,KeyError,TypeError) as error:
            return None,VerificationRecord(False,"plan artifact/verifier",stderr=str(error))
    def solve(self,X,allow_learning=False,validations=()):
        from .retrieval import RetrievalResult
        context=self.context(X,validations)
        eligible_ids=tuple(s.identity for s in self.domain.retrieval_candidates(self.library))
        preferred_structures=self.retrieval_search_exposure('ACTIVE')
        retired_structures=self.retrieval_search_exposure('RETIRED')
        _,_,classes_seen=self.operation_pool.get_search_partition(self.domain,eligible_ids)
        preferred=tuple(s.identity for s in preferred_structures)
        retired=tuple(s.identity for s in retired_structures)
        phase_metrics={"preferred":[],"retired":[]};anchors=[];retrieval=None;successful_retired=False
        for phase,ids in (("preferred",preferred),("retired",retired)):
            pending=list(ids)
            while pending:
                context.retrieval_scope=frozenset(pending)
                try:current=self.retrieval.retrieve(context)
                finally:context.retrieval_scope=None
                phase_metrics[phase].append(current.metrics)
                if current.anchors:anchors.extend(current.anchors)
                if current.selected is None:
                    retrieval=current
                    break
                selected_id=current.selected.identity
                if selected_id not in pending:raise RuntimeError("retrieval selected outside the active preference stage")
                pending.remove(selected_id)
                attempt=self._reuse(context,current)
                if attempt.status=="FULL_HIT":
                    successful_retired=phase=="retired"
                    retrieval_metrics={"preferred_candidates":len(preferred),"retired_candidates_deferred":len(retired),
                                       "retired_fallback_used":successful_retired,"retired_fallback_attempted":bool(phase_metrics["retired"]),
                                       "equivalence_classes_seen":classes_seen,"active_switches":attempt.metrics.get("retrieval",{}).get("active_switches",0),
                                       "stage1":phase_metrics["preferred"],"stage2":phase_metrics["retired"]}
                    for key in ("before","after","alpha_evaluations","wall_seconds"):
                        retrieval_metrics[key]=sum(row.get(key,0) for rows in phase_metrics.values() for row in rows)
                    retrieval_metrics["method"]=current.metrics.get("method",self.retrieval.__class__.__name__)
                    for key,value in current.metrics.items():
                        if key not in ("before","after","alpha_evaluations","wall_seconds"):
                            retrieval_metrics.setdefault(key,value)
                    attempt.metrics["retrieval"]=retrieval_metrics
                    attempt.metrics["preference"]={k:retrieval_metrics[k] for k in ("preferred_candidates","retired_candidates_deferred","retired_fallback_used","equivalence_classes_seen","active_switches")}
                    context.timing["retrieval"]=retrieval_metrics
                    return attempt
                context.trace.append({"phase":"preference_candidate_rejected","structure_id":selected_id,"status":attempt.status})
            if not ids:phase_metrics[phase]=[]
        if retrieval is None:
            retrieval=RetrievalResult("TRUE_LIBRARY_MISS",anchors=tuple(anchors),metrics={"method":self.retrieval.__class__.__name__})
        retrieval.anchors=tuple(anchors)
        retrieval_metrics={"preferred_candidates":len(preferred),"retired_candidates_deferred":len(retired),
                           "retired_fallback_used":False,"retired_fallback_attempted":bool(phase_metrics["retired"]),
                           "equivalence_classes_seen":classes_seen,"active_switches":0,
                           "stage1":phase_metrics["preferred"],"stage2":phase_metrics["retired"]}
        for key in ("before","after","alpha_evaluations","wall_seconds"):
            retrieval_metrics[key]=sum(row.get(key,0) for rows in phase_metrics.values() for row in rows)
        retrieval_metrics["method"]=retrieval.metrics.get("method",self.retrieval.__class__.__name__)
        retrieval.metrics=retrieval_metrics;context.timing['retrieval']=retrieval_metrics
        countermodel=self.domain.countermodel(context.X)
        if countermodel is not None:return SolveResult("NO_FEASIBLE_PROOF",context=context,metrics={"countermodel":countermodel})
        if allow_learning:return self.formation.learn(context,retrieval)
        plan=self.planner.plan(context,retrieval.anchors)
        if not plan.success:return SolveResult(plan.status,context=context,metrics={"depth":plan.metrics,"retrieval":retrieval.metrics})
        artifact,verification=self._verify_plan(plan,context)
        if (not verification.passed and plan.metrics.get("preference_stage")=="preferred"
                and plan.metrics.get("retired_candidates_deferred",0)>0):
            preferred_metrics=dict(plan.metrics);context.trace.append({"phase":"preferred_plan_verification_failed"})
            fallback=self.planner.plan_fallback(context,retrieval.anchors);fallback_artifact=None;fallback_verification=None
            if fallback.success:fallback_artifact,fallback_verification=self._verify_plan(fallback,context)
            retired_ids={s.identity for s in self.search_exposure('RETIRED')}
            used=bool(fallback.success and fallback_verification and fallback_verification.passed
                      and any(a.structure_id in retired_ids for a in fallback.actions))
            fallback=self.planner.combine_verification_fallback(preferred_metrics,fallback,used)
            if fallback.success:
                plan=fallback;artifact=fallback_artifact;verification=fallback_verification
            else:
                plan=fallback
        if verification.passed:
            usage_event=self.usage.record_successful_path((a.structure_id for a in plan.actions),verification)
            context.trace.append({"phase":"usage_update",**usage_event})
        return SolveResult("COMPLETION_VERIFIED" if verification.passed else "VERIFY_FAIL",artifact.value if artifact else None,
                           verification=verification,context=context,metrics={"depth":plan.metrics,"retrieval":retrieval.metrics,
                           "active_switches":usage_event["active_switches"] if verification.passed else 0})
    def learn(self,X,validations=()):return self.solve(X,allow_learning=True,validations=validations)
    def execute(self,structure,X,alpha=None):
        context=self.context(X)
        if alpha is None:
            match=self.domain.structural_exact(structure,context.X,context)
            if not match.exact:raise ValueError("structure is not exactly applicable")
            alpha=self.alpha_solver.solve(structure,context.X,context)
            if alpha is None:raise ValueError("alpha solver rejected instantiation")
        context.alpha=alpha;return structure.execute(context.X,alpha,context)
    @staticmethod
    def usage_sidecar_path(knowledge_path):
        from pathlib import Path
        path=Path(knowledge_path)
        return str(path.with_suffix(".usage.json")) if path.suffix.lower()==".json" else str(path)+".usage.json"
    def save_knowledge(self,path):
        self.library.save(path);return self.usage.save(self.usage_sidecar_path(path))
    def load_usage_sidecar(self,knowledge_path):
        from pathlib import Path
        from .preference import UsageRegistry
        sidecar=Path(self.usage_sidecar_path(knowledge_path))
        if not sidecar.exists():
            self.usage=UsageRegistry(self.library)
            from .operation_pool import OperationPoolProvider
            self.operation_pool=OperationPoolProvider(self.library,self.usage)
            return False
        self.usage=UsageRegistry.load(sidecar,self.library)
        from .operation_pool import OperationPoolProvider
        self.operation_pool=OperationPoolProvider(self.library,self.usage)
        return True
    def set_backend(self,backend):
        from .backend import Backend,CUDABackend
        from .config import BackendConfig
        if not isinstance(backend,Backend):raise TypeError("Backend implementation required")
        self.backend=backend;self._alpha_backend=None
        self.backend_config=BackendConfig(alpha_width='cuda' if isinstance(backend,CUDABackend) else 'cpu')
