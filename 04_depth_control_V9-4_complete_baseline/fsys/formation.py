from dataclasses import replace
import time
from .structure import Composite
from .provenance import FormationProvenance
from .serialization import digest, thaw


def canonical_compose(children, topology, definition, parent_levels, *, source="ordinary composition",
                      evidence=None, max_level=None, slot_capacity=None):
    """The single constructor for reusable composite F objects.

    Domain semantics supply the executable definition and formal slots;
    discovery supplies ordered child identities and topology only. Provenance
    and evidence do not participate in the operation identity.
    """
    children = tuple(children)
    if not children:
        raise ValueError("composition requires at least one child")
    missing = [child for child in children if child not in parent_levels]
    if missing:
        raise ValueError("composition references unknown child: " + ", ".join(missing))
    if not isinstance(definition, dict):
        raise TypeError("composition definition must be a mapping")
    required = {"operation_id", "ports", "alpha_schema", "metadata"}
    if not required.issubset(definition):
        raise ValueError("composition definition is incomplete: " + ", ".join(sorted(required - definition.keys())))
    level = 1 + max(parent_levels[child] for child in children)
    alpha_schema = definition["alpha_schema"]
    if max_level is not None and level > max_level:
        raise ValueError("architecture level capacity exceeded")
    if slot_capacity is not None and len(alpha_schema.binding_slots) > slot_capacity:
        raise ValueError("slot capacity exceeded")
    ports = tuple(definition["ports"])
    kernel = dict(definition.get("kernel", {}))
    kernel.setdefault("composition_topology", topology)
    metadata = dict(definition["metadata"])
    operation_id = definition["operation_id"]
    identity = "F_" + digest({"operation_id": operation_id, "children": children,
        "topology": topology, "metadata": metadata,
        "alpha_schema": alpha_schema.to_dict(), "kernel": kernel,
        "ports": [port.to_dict() for port in ports]})[:20]
    provenance = FormationProvenance(source, children, details=evidence or {})
    return Composite(identity, operation_id, ports, alpha_schema, children, level,
                     provenance, kernel, metadata)

class FormationEngine:
    def __init__(self,engine):self.engine=engine
    def construct(self,definition,parents,source="autonomous composition"):
        parents=tuple(parents)
        metadata=definition.get("metadata",{})
        kernel=definition.get("kernel",{})
        topology=definition.get("topology",metadata.get("wiring",metadata.get("call_plan",kernel.get("composition_topology",{}))))
        return self.canonical_compose(parents,topology,definition,source=source)
    def canonical_compose(self,children,topology,definition,*,source="ordinary composition",evidence=None):
        engine=self.engine
        return canonical_compose(children,topology,definition,
            {s.identity:s.level for s in engine.library},source=source,evidence=evidence,
            max_level=engine.arch.max_level,slot_capacity=engine.arch.slot_capacity)
    def compose_discovered(self,proposal,*,source="autonomous discovery",evidence=None):
        """Turn a children/topology proposal into an ordinary domain F."""
        children=tuple(proposal["children"]);topology=proposal["topology"]
        provider=getattr(self.engine.domain,"composition_definition_from_children",None)
        if provider is None:raise TypeError("domain has no canonical composition definition provider")
        definition=provider(children,topology)
        return self.canonical_compose(children,topology,definition,source=source,evidence=evidence)
    def _prepare_verified_candidate(self,context,plan):
        try:
            domain=context.domain;definition=domain.abstract_instance(context.X,plan,context)
            candidate=self.construct(definition,[a.structure_id for a in plan.actions])
            candidate,artifact=domain.prepare_candidate(candidate,plan,context)
        except (ValueError,KeyError,TypeError) as error:
            return None,None,None,"FORMATION_REJECTED",str(error)
        if not domain.audit_structure(candidate):return candidate,artifact,None,"FORMATION_REJECTED","domain persistent audit failed"
        try:verification=self.engine.verifier.verify(artifact,context)
        except (ValueError,KeyError,TypeError) as error:
            return candidate,artifact,None,"VERIFY_FAIL",str(error)
        if not verification.passed:return candidate,artifact,verification,"VERIFY_FAIL",verification.stderr or "verification failed"
        return candidate,artifact,verification,"FORMED",""
    def learn(self,context,retrieval):
        from .runtime import SolveResult
        domain=context.domain;start=time.perf_counter();plan=self.engine.planner.plan(context,retrieval.anchors)
        if not plan.success:return SolveResult(plan.status,context=context,metrics={"retrieval":retrieval.metrics,"depth":plan.metrics})
        candidate,artifact,verification,status,reason=self._prepare_verified_candidate(context,plan)
        if status!="FORMED" and plan.metrics.get("preference_stage")=="preferred" and plan.metrics.get("retired_candidates_deferred",0)>0:
            preferred_metrics=dict(plan.metrics);context.trace.append({"phase":"preferred_formation_failed","status":status,"reason":reason})
            fallback=self.engine.planner.plan_fallback(context,retrieval.anchors)
            if fallback.success:
                candidate,artifact,verification,status,reason=self._prepare_verified_candidate(context,fallback)
            used=bool(status=="FORMED" and fallback.success
                      and any(a.structure_id in self.engine.usage.retired_ids() for a in fallback.actions))
            fallback=self.engine.planner.combine_verification_fallback(preferred_metrics,fallback,used)
            plan=fallback
        if status!="FORMED":
            if status=="FORMATION_REJECTED":context.trace.append({'phase':'formation_rejected','reason':reason})
            return SolveResult(status,artifact.value if artifact else None,
                               verification=verification,context=context,metrics={'reason':reason,'retrieval':retrieval.metrics,'depth':plan.metrics})
        provenance=FormationProvenance("autonomous residual completion",candidate.parents,verification.verifier,verification.source_digest)
        candidate=replace(candidate,formation_provenance=provenance)
        self.engine.library.commit(candidate,verification)
        context.trace.append({"phase":"successful_formation","success":True,
            "compositions":[{"children":candidate.parents,
                "topology":thaw(candidate.kernel.get("composition_topology",{}))}]})
        usage_event=self.engine.usage.record_successful_path((a.structure_id for a in plan.actions),verification)
        context.trace.append({"phase":"usage_update",**usage_event})
        context.trace.append({"phase":"commit","structure_id":candidate.identity,"level":candidate.level,"hash":candidate.persistent_hash})
        return SolveResult("FORMED",artifact.value,candidate.identity,candidate.parents,candidate.level,candidate.persistent_hash,
                           verification,context,{"retrieval":retrieval.metrics,"depth":plan.metrics,"formation_seconds":time.perf_counter()-start,
                                                 "verification_seconds":verification.elapsed_seconds,
                                                 "active_switches":usage_event["active_switches"]})
    def compose(self,left,right,wiring,context):
        # Composition candidate is still uncommitted until the common verification gate.
        if not wiring:raise ValueError("composition requires wiring")
        if any(len(edge)!=2 for edge in wiring):raise ValueError("wiring edges must have two port names")
        if len({edge[1] for edge in wiring})!=len(wiring):raise ValueError("multiple drivers on one input")
        for edge in wiring:
            a=next(p for p in left.ports if p.name==edge[0]);b=next(p for p in right.ports if p.name==edge[1])
            if not context.domain.port_compatible(a,b):raise ValueError("incompatible composition ports")
        definition=context.domain.composition_definition(left,right,wiring)
        return self.construct(definition,(left.identity,right.identity),"explicit composition")
