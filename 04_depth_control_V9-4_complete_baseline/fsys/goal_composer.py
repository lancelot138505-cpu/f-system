"""Goal-driven search over first-class operations through public contracts."""
from __future__ import annotations

import time
from dataclasses import dataclass

from .alpha import AlphaSchema
from .ports import Port
from .public_operations import bind_operation, compatible_operations
from .operation_pool import OperationPoolProvider
from .serialization import FrozenMap
from .structure import Structure


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    inputs: tuple[dict,...]
    output: dict
    examples: tuple[dict,...]
    goal_signature: str
    def signature(self):
        from .serialization import digest
        return digest({'inputs':self.inputs,'output':self.output,'goal_signature':self.goal_signature})


class _DeferredKVStructure:
    """Public candidate shell; serialized internals load only after SELECT."""
    def __init__(self,composer,interface,serialized):
        self.identity=interface.identity
        self.structure_id=interface.identity
        self.operation_id=interface.operation_id
        self.ports=tuple(Port(p.name,p.direction,p.dtype,p.cardinality) for p in interface.ports)
        self.alpha_schema=AlphaSchema(interface.formal_slots)
        self.metadata=FrozenMap({'public_operation_interface':interface.to_dict()})
        self._composer=composer
        self._serialized=serialized
        self._loaded=None

    def _load(self):
        allowed=(self.identity in self._composer._selected_ids
            or self.identity in self._composer._active_descendant_ids
            or self._composer._post_search_execution)
        if not allowed:
            raise AssertionError(f'KV operation internals read before SELECT: {self.identity}')
        if self._loaded is None:
            self._loaded=Structure.from_dict(self._serialized)
            self._composer.search_trace.append({'event':'MATERIALIZE_SELECTED_KV_OPERATION',
                'structure_id':self.identity,'after_select':True})
            if (self.identity==self._composer._active_root_id
                    or self.identity in self._composer._active_descendant_ids):
                self._composer._active_descendant_ids.update(self._loaded.parents)
        return self._loaded

    def __getattr__(self,name):
        return getattr(self._load(),name)


class _OperationSearchLibrary:
    """Read-only overlay for deferred KV structures in the existing Planner."""
    def __init__(self,base,composer,interfaces,serialized,persistent_ids):
        self.base=base
        self.deferred={identity:_DeferredKVStructure(composer,interfaces[identity],serialized[identity])
                       for identity in interfaces if identity not in persistent_ids}

    def __iter__(self):
        return iter((*tuple(self.base),*tuple(self.deferred.values())))

    def __len__(self):
        return len(self.base)+len(self.deferred)

    def __getitem__(self,identity):
        try:return self.base[identity]
        except KeyError:return self.deferred[identity]


class GoalDrivenComposer:
    """Retrieve, bind, and expand operations without inspecting formation internals."""
    ALLOWED_KV_STATUSES=frozenset(('VALIDATED','PROMOTABLE','PERSISTENT'))

    def __init__(self,library,external_kv,*,max_candidates=1200,public_contracts=None,
                 operation_pool=None,usage_registry=None):
        self.library=library
        self.kv=external_kv
        self.max_candidates=max_candidates
        self.public_contracts=dict(public_contracts or {})
        self.operation_pool=operation_pool or OperationPoolProvider(
            library,usage_registry,public_contracts=self.public_contracts)
        if self.operation_pool.library is not library:
            raise ValueError('Composer and OperationPoolProvider must use the same Library object')
        self.search_trace=[]
        self.retrieval=[]
        self.nodes=0
        self.started=None
        self._interfaces={}
        self._kv_structures={}
        self._selected_ids=set()
        self._active_root_id=None
        self._active_descendant_ids=set()
        self._post_search_execution=False

    def retrieve_operations(self,goal,*,excluded_ids=(),lifecycle_view='ACTIVE'):
        """Return type-compatible public operations exposed by the pool provider."""
        if self.operation_pool.operation_source is not self.library:
            self.operation_pool=self.operation_pool.with_operation_source(self.library)
        records=getattr(self.kv,'records',{}).values()
        extras=[record.get('structure',{}) for record in records
                if record.get('status') in self.ALLOWED_KV_STATUSES]
        compatible=self.operation_pool.get_operations(goal,
            lifecycle_view=lifecycle_view,excluded_ids=excluded_ids,
            extra_operations=extras)
        self._interfaces={item.identity:item for item in compatible}
        persistent_ids=self.operation_pool.persistent_ids()
        self._kv_structures={}
        for raw in extras:
            sid=raw.get('structure_id',raw.get('identity')) if isinstance(raw,dict) else None
            if sid in self._interfaces and sid not in persistent_ids:
                self._kv_structures[sid]=raw
        self.retrieval=[item.to_dict() for item in compatible]
        deficiencies=[]
        self.search_trace.append({'event':'RETRIEVE_OPERATIONS',
            'goal_signature':goal.goal_signature,
            'candidate_structures':[{'structure_id':item.identity,
                'operation_id':item.operation_id,'capability_descriptor':item.capability_descriptor}
                for item in compatible],
            'interface_deficiencies':deficiencies})
        for item in compatible:
            self.search_trace.append({'event':'RETRIEVE_OPERATION',
                'structure_id':item.identity,'operation_id':item.operation_id,
                'capability_descriptor':item.capability_descriptor})
            self.search_trace.append({'event':'COMPATIBLE_OPERATION',
                'structure_id':item.identity,'operation_id':item.operation_id,
                'public_contract_digest':self._contract_digest(item)})
        return compatible

    @staticmethod
    def _contract_digest(interface):
        from .serialization import digest
        return digest(interface.to_dict())

    def compatible_operations(self,operations,goal):
        return compatible_operations(operations,goal)

    def bind_operation(self,interface,goal,runtime_values=None):
        return bind_operation(interface,goal,runtime_values)

    def candidates(self,goal,*,start_rank=0,excluded_ids=(),lifecycle_view='ACTIVE'):
        from .recipe_search import recipe_candidates
        self.search_metrics={'candidate_attempts':0,'binding_combinations':0,
            'composition_expansions':0,'intermediate_materializations':0}
        self.started=time.perf_counter();rank=0
        for interface in self.retrieve_operations(goal,excluded_ids=excluded_ids,
                                                  lifecycle_view=lifecycle_view):
            rank+=1
            if rank<=start_rank:continue
            if rank>self.max_candidates:
                self.search_trace.append({'event':'SEARCH_EXPLOSION','limit':self.max_candidates})
                return
            bindings=self.bind_operation(interface,goal)
            self.search_metrics['candidate_attempts']+=1
            self.search_metrics['binding_combinations']+=1
            self.nodes+=1
            self.search_trace.append({'event':'ENUMERATE_PUBLIC_BINDING',
                'structure_id':interface.identity,'bindings':bindings})
            path=({'kind':'operation_selection','structure_id':interface.identity},)
            yield {'candidate_kind':'operation_edge','rank':rank,
                'structure_id':interface.identity,'operation_id':interface.operation_id,
                'root_id':interface.identity,'step_id':None,'candidate_ids':{},
                'search_depth':len(path),'derivation_path':path,
                'bindings':bindings,'public_operation':interface.to_dict()}
        yield from recipe_candidates(self,goal,start_rank=start_rank,rank=rank,
            excluded_ids=excluded_ids,lifecycle_view=lifecycle_view)

    def execute_candidate(self,candidate,goal,args,*,runtime_context=None):
        """Select and publicly bind an edge before loading its implementation."""
        from .recursive_f_runtime import RecursiveFExecutor
        sid=candidate['root_id']
        self.search_trace.append({'event':'SELECT_OPERATION','structure_id':sid,
            'search_depth':candidate['search_depth']})
        interface=self._interfaces[sid]
        bindings=bind_operation(interface,goal,args,runtime_context)
        self.search_trace.append({'event':'BIND_PUBLIC_SLOTS','structure_id':sid,
            'bindings':bindings,'formal_slots':list(interface.formal_slots)})
        self.search_trace.append({'event':'EXECUTE_OPERATION','structure_id':sid})
        is_binary=candidate.get('candidate_kind')=='public_binary_operation_composition'
        if is_binary:
            for role,child_id in zip(('left','right'),candidate['child_operation_ids']):
                child_interface=self._interfaces[child_id]
                child_bindings=bind_operation(child_interface,goal,args,runtime_context)
                self.search_trace.append({'event':'SELECT_OPERATION',
                    'structure_id':child_id,'operation_id':child_interface.operation_id,
                    'selected_as':'child','parent_id':sid,'role':role})
                self.search_trace.append({'event':'BIND_PUBLIC_SLOTS',
                    'structure_id':child_id,'bindings':child_bindings,
                    'formal_slots':list(child_interface.formal_slots),
                    'selected_as':'child','parent_id':sid})
        executor=RecursiveFExecutor(self.library,self.kv,trace=is_binary)
        result=executor.execute_public(interface,bindings)
        if is_binary:
            direct=[event for event in executor.dispatches if event.get('parent')==sid]
            for event in direct:
                self.search_trace.append({'event':'USE_AS_CHILD_OPERATION',
                    'parent_id':sid,'structure_id':event['structure_id'],
                    'operation_id':event['operation_id'],'dispatch_depth':event['depth'],
                    'selected_after_parent':True})
        return result

    def search_operations(self,goal,engine,X,*,excluded_ids=(),lifecycle_view='ACTIVE'):
        """Feed the public operation pool into the existing structural planner."""
        if engine.library is not self.library:
            raise ValueError('Composer and Engine must use the same Library object')
        self.operation_pool=OperationPoolProvider(engine.library,engine.usage,
            public_contracts=self.public_contracts)
        self.search_trace=[];self.started=time.perf_counter()
        pool=self.retrieve_operations(goal,excluded_ids=excluded_ids,
                                      lifecycle_view=lifecycle_view)
        ids=frozenset(item.identity for item in pool)
        context=engine.context(X)
        context.library=_OperationSearchLibrary(self.library,self,self._interfaces,
            self._kv_structures,self.operation_pool.persistent_ids())
        context.operation_edge_observer=self._observe_edge
        self._active_interfaces={item.identity:item for item in pool}
        self._active_goal=goal
        self._selected_ids=set();self._active_root_id=None;self._active_descendant_ids=set()
        self._post_search_execution=False
        self.nodes=0
        initial_composition_expansions=context.counters.get('composition_expansions',0)
        plan=engine.planner._plan_stage(context,(),ids)
        self.nodes=plan.metrics.get('expansions',0)
        search_metrics={
            'candidate_attempts':plan.metrics.get('candidate_probes',0),
            'composition_expansions':context.counters.get('composition_expansions',0)-initial_composition_expansions,
            'search_depth':len(plan.actions),
        }
        selected=[action.structure_id for action in plan.actions]
        self.search_trace.append({'event':'SEARCH_RESULT','success':plan.success,
            'selected_path':selected,'search_depth':len(plan.actions),
            'candidate_attempts':plan.metrics.get('candidate_probes',0)})
        if plan.success:self._post_search_execution=True
        artifact,verification=engine._verify_plan(plan,context) if plan.success else (None,None)
        self._post_search_execution=False
        if plan.success:
            self.search_trace.append({'event':'VERIFICATION_ATTEMPT',
                'passed':bool(verification and verification.passed),
                'verifier':verification.verifier if verification else None})
        return {'plan':plan,'context':context,'artifact':artifact,
                'verification':verification,'public_pool':pool,'search_metrics':search_metrics}

    def _observe_edge(self,stage,action,state,child=None,depth=0):
        structure_id=action.structure_id
        interface=getattr(self,'_active_interfaces',{}).get(structure_id)
        if interface is None:
            # The observer is installed on the search context; IDs were already
            # checked against the public pool before planning began.
            interface=self._active_interfaces[structure_id]
        goal=self._active_goal
        if stage=='before':
            self._selected_ids.add(structure_id)
            self._active_root_id=structure_id
            self._active_descendant_ids=set()
            self.search_trace.append({'event':'SELECT_OPERATION',
                'structure_id':structure_id,'operation_id':interface.operation_id,
                'search_depth':depth})
            runtime_values=(tuple(state),) if len(interface.formal_slots)==1 else tuple(state)
            bindings=self.bind_operation(interface,goal,runtime_values)
            self.search_trace.append({'event':'BIND_PUBLIC_SLOT',
                'structure_id':structure_id,'bindings':bindings})
        else:
            self.search_trace.append({'event':'EXPAND_OPERATION_EDGE',
                'structure_id':structure_id,'search_depth':depth})
            self._active_root_id=None
            self._active_descendant_ids=set()

    @staticmethod
    def _bind(value,operations,parameters):
        """Compatibility helper for public recipe interpreters outside search."""
        if isinstance(value,dict):
            if set(value)=={'operation'}:return operations[value['operation']]
            if set(value)=={'parameter'}:return parameters[value['parameter']]
            return {key:GoalDrivenComposer._bind(child,operations,parameters)
                    for key,child in value.items()}
        if isinstance(value,list):
            return [GoalDrivenComposer._bind(child,operations,parameters) for child in value]
        return value
