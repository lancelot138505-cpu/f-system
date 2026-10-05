"""Domain-independent, CPU-only structural planner. No alpha optimizer is imported."""
import heapq
import itertools
import time
from dataclasses import dataclass

@dataclass
class PlanResult:
    success: bool
    actions: tuple
    status: str
    metrics: dict

class DepthPlanner:
    def __init__(self,indexed=True,cache=True):self.indexed,self.cache=indexed,cache
    @staticmethod
    def operation_pool_ids(context,lifecycle_view='ACTIVE'):
        return tuple(item.identity for item in context.engine.operation_pool.get_search_structures(
            context.domain,lifecycle_view=lifecycle_view))

    def plan(self,context,anchors=(),*,lifecycle_view='ACTIVE'):
        provider=context.engine.operation_pool
        if lifecycle_view in ('FULL','RETIRED'):
            ids=self.operation_pool_ids(context,lifecycle_view)
            result=self._plan_stage(context,anchors,frozenset(ids))
            result.metrics.update({"preferred_candidates":len(ids) if lifecycle_view=='ACTIVE' else 0,
                "retired_candidates_deferred":len(ids) if lifecycle_view=='RETIRED' else 0,
                "retired_fallback_used":lifecycle_view=='RETIRED' and any(a.structure_id in ids for a in result.actions),
                "equivalence_classes_seen":provider.lifecycle_summary()['equivalence_classes'],
                "active_switches":0,"preference_stage":lifecycle_view.lower()})
            return result
        preferred=self.operation_pool_ids(context,'ACTIVE')
        retired=self.operation_pool_ids(context,'RETIRED')
        classes_seen=provider.get_search_partition(context.domain,
            tuple((*preferred,*retired)))[2]
        if not retired:
            result=self._plan_stage(context,anchors,frozenset(preferred))
            result.metrics.update({"preferred_candidates":len(preferred),"retired_candidates_deferred":0,
                                   "retired_fallback_used":False,"equivalence_classes_seen":classes_seen,"active_switches":0,
                                   "preference_stage":"preferred"})
            return result
        first=self._plan_stage(context,anchors,frozenset(preferred))
        if first.success:
            first.metrics.update({"preferred_candidates":len(preferred),"retired_candidates_deferred":len(retired),
                                  "retired_fallback_used":False,"equivalence_classes_seen":classes_seen,"active_switches":0,
                                  "preference_stage":"preferred"})
            return first
        retired_ids=set(self.operation_pool_ids(context,'RETIRED'))
        fallback=self._plan_stage(context,anchors,frozenset(retired_ids))
        used=bool(fallback.success and any(a.structure_id in retired_ids for a in fallback.actions))
        numeric_sum=("expansions","legal_actions","repeated_states","dead_end_expansions","candidate_probes","structural_checks","cache_hits","whole_F_calls","primitive_calls")
        metrics={k:first.metrics.get(k,0)+fallback.metrics.get(k,0) for k in numeric_sum}
        metrics.update({"candidate_generation_seconds":first.metrics.get("candidate_generation_seconds",0.)+fallback.metrics.get("candidate_generation_seconds",0.),
                        "ranking_seconds":first.metrics.get("ranking_seconds",0.)+fallback.metrics.get("ranking_seconds",0.),
                        "wall_seconds":first.metrics.get("wall_seconds",0.)+fallback.metrics.get("wall_seconds",0.),
                        "wall_seconds_per_expansion":(first.metrics.get("wall_seconds",0.)+fallback.metrics.get("wall_seconds",0.))/max(1,metrics["expansions"]),
                        "index_enabled":self.indexed,"cache_enabled":self.cache,"alpha_updates_in_depth":0,
                        "preferred_candidates":len(preferred),"retired_candidates_deferred":len(retired),
                        "retired_fallback_used":used,"equivalence_classes_seen":classes_seen,"active_switches":0,
                        "preference_stage":"retired_fallback" if used else "full_fallback",
                        "preferred_stage":first.metrics,"fallback_stage":fallback.metrics})
        return PlanResult(fallback.success,fallback.actions,fallback.status,metrics)

    def plan_fallback(self,context,anchors=(),*,lifecycle_view='RETIRED'):
        """Reopen all committed knowledge after a preferred plan fails artifact verification."""
        provider=context.engine.operation_pool
        preferred=self.operation_pool_ids(context,'ACTIVE')
        retired=self.operation_pool_ids(context,'RETIRED')
        classes_seen=provider.get_search_partition(context.domain,
            tuple((*preferred,*retired)))[2]
        ids=self.operation_pool_ids(context,lifecycle_view)
        result=self._plan_stage(context,anchors,frozenset(ids))
        used=bool(result.success and any(a.structure_id in retired for a in result.actions))
        result.metrics.update({"preferred_candidates":len(preferred),"retired_candidates_deferred":len(retired),
                               "retired_fallback_used":used,"equivalence_classes_seen":classes_seen,
                               "active_switches":0,"preference_stage":"retired_fallback" if used else "full_fallback"})
        return result

    @staticmethod
    def combine_verification_fallback(preferred_metrics,fallback,used):
        """Keep both planner stages visible and sum their actual work counters."""
        second=dict(fallback.metrics);first=dict(preferred_metrics)
        summed=("expansions","legal_actions","repeated_states","dead_end_expansions","candidate_probes",
                "structural_checks","cache_hits","whole_F_calls","primitive_calls")
        for key in summed:fallback.metrics[key]=first.get(key,0)+second.get(key,0)
        for key in ("candidate_generation_seconds","ranking_seconds","wall_seconds"):
            fallback.metrics[key]=first.get(key,0.)+second.get(key,0.)
        fallback.metrics["wall_seconds_per_expansion"]=fallback.metrics["wall_seconds"]/max(1,fallback.metrics["expansions"])
        fallback.metrics.update({"preferred_stage":first,"verification_fallback_stage":second,
                                 "retired_fallback_used":bool(used),
                                 "preference_stage":"retired_fallback" if used else "full_fallback_after_verification_failure"})
        return fallback

    def _plan_stage(self,context,anchors,allowed_ids):
        domain=context.domain;arch=context.engine.arch;st=time.perf_counter();generation=ranking=0.
        initial_counts=dict(context.counters)
        context.cache_enabled=self.cache;context.index_enabled=self.indexed
        initial=domain.initial_state(context.X);visited={domain.state_key(initial):0};serial=itertools.count(1)
        queue=[(domain.initial_priority(initial,context),0,initial,())];expansions=repeated=legal=dead=0;solution=None
        while queue and expansions<arch.expansion_budget:
            _,_,state,path=heapq.heappop(queue)
            context.current_state=state
            if domain.is_complete(state,context):solution=path;break
            if len(path)>=arch.max_depth:continue
            expansions+=1;t=time.perf_counter();previous_scope=context.search_scope;context.search_scope=allowed_ids
            try:actions=domain.search_actions(state,context)
            finally:context.search_scope=previous_scope
            if allowed_ids is not None:actions=[a for a in actions if a.structure_id in allowed_ids]
            generation+=time.perf_counter()-t;legal+=len(actions);dead+=int(not actions)
            ranked=[];t=time.perf_counter()
            for action in actions:
                observer=context.operation_edge_observer
                if observer is not None:observer('before',action,state,depth=len(path)+1)
                child=domain.step(state,action,context);depth=len(path)+1;key=domain.state_key(child)
                if observer is not None:observer('after',action,state,child,depth)
                priority=domain.action_priority(state,child,action,depth,context,anchors)
                ranked.append((priority,action,child,key,depth))
            ranked.sort(key=lambda row:row[0]);ranking+=time.perf_counter()-t
            for priority,action,child,key,depth in ranked[:arch.search_width]:
                if key in visited and depth>=visited[key]:repeated+=1;continue
                visited[key]=depth;heapq.heappush(queue,(priority,next(serial),child,path+(action,)))
        if solution is None and queue and domain.is_complete(queue[0][2],context):solution=queue[0][3]
        elapsed=time.perf_counter()-st
        metrics={"expansions":expansions,"candidate_generation_seconds":generation,"ranking_seconds":ranking,
                 "wall_seconds":elapsed,"wall_seconds_per_expansion":elapsed/max(1,expansions),
                 "legal_actions":legal,"repeated_states":repeated,"dead_end_expansions":dead,
                 "candidate_probes":context.counters.get("candidate_probes",0)-initial_counts.get("candidate_probes",0),"structural_checks":context.counters.get("structural_checks",0)-initial_counts.get("structural_checks",0),
                 "cache_hits":context.counters.get("cache_hits",0)-initial_counts.get("cache_hits",0),"index_enabled":self.indexed,"cache_enabled":self.cache,
                 "whole_F_calls":sum(bool(context.library[a.structure_id].parents) for a in solution or ()),
                 "primitive_calls":sum(not context.library[a.structure_id].parents for a in solution or ()),
                 "alpha_updates_in_depth":0}
        return PlanResult(solution is not None,solution or (),"SUCCESS" if solution is not None else "SELECTOR_BUDGET_EXHAUSTED" if queue else "SELECTOR_NO_PATH",metrics)
