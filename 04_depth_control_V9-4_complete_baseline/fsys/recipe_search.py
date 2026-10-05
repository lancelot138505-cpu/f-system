"""V9-1 recipe enumeration retained as ordinary residual composition search."""
from __future__ import annotations
import itertools
from .alpha import AlphaSchema
from .formation import canonical_compose
from .serialization import thaw, digest
from .structure import Structure
from .token_goal_domain import recursive_operation_contract,recursive_step_operation_contract
from .public_operations import normalize_operation
from .ports import Port


def _ordered_executable_refs(node):
    """Return direct F calls in an executable graph in runtime order."""
    refs=[]
    def visit(value):
        if isinstance(value,dict):
            for key,child in value.items():
                if key in ('f','body_f','left_f','right_f') and isinstance(child,str):
                    refs.append(child)
                visit(child)
        elif isinstance(value,(list,tuple)):
            for child in value:visit(child)
    visit(node)
    return tuple(dict.fromkeys(refs))


def _direct_operation_bindings(bindings,direct_refs):
    """Align a recipe receipt with only the operations directly called by one F."""
    refs=set(direct_refs)
    selected={name:identity for name,identity in bindings.items()
              if isinstance(identity,str) and identity in refs}
    if set(selected.values())!=refs or len(selected)!=len(refs):
        raise ValueError('generic recipe executable references do not match its operation bindings')
    return selected


def recipe_candidates(composer, goal, *, start_rank=0, rank=0, excluded_ids=(),
                      lifecycle_view='ACTIVE'):
    if not hasattr(composer.library,'__getitem__'):return
    excluded=set(excluded_ids)
    basis=composer.operation_pool.get_recipe_basis(lifecycle_view=lifecycle_view)
    operations=[item for item in basis.operations if item.identity not in excluded]
    levels={item.identity:item.level for item in operations}
    topologies=[item for item in basis.topologies if item.identity not in excluded]
    levels.update({item.identity:item.level for item in topologies})
    caps={}
    for operation in operations:
        caps.setdefault(operation.capability,[]).append(operation)
    composer.search_trace.append({'event':'RESIDUAL_RECIPE_SEARCH','after_operation_pool':True})
    for topology in topologies:
        recipe=topology.recipe
        if not recipe:continue
        required=recipe.get('required_capabilities',[])
        if any(cap not in caps for cap in required):continue
        selected={cap:sorted(caps[cap],key=lambda s:s.identity)[0].identity for cap in required}
        domains=recipe.get('binding_schema',{});names=tuple(domains)
        composer.search_trace.append({'event':'SELECT_TOPOLOGY','structure_id':topology.identity,'search_depth':1})
        for values in itertools.product(*(domains[name] for name in names)):
            rank+=1
            if rank<=start_rank:continue
            if rank>composer.max_candidates:return
            parameters=dict(zip(names,values))
            path=({'kind':'topology_selection','structure_id':topology.identity},
                  {'kind':'binding_combination','bindings':parameters})
            composer.nodes+=1
            composer.search_metrics['candidate_attempts']+=1
            composer.search_metrics['binding_combinations']+=1
            composer.search_trace.append({'event':'ENUMERATE_RECIPE_BINDING','bindings':parameters,'search_depth':2})
            parents=tuple(dict.fromkeys((topology.identity,*selected.values())))
            replay={'recipe_version':recipe['recipe_version'],'topology_id':topology.identity,
                    'parameters':parameters}
            formed={}
            for label in ('step','root'):
                bindings=selected if label=='step' else selected|{'step':formed['step'].identity}
                kernel=composer._bind(recipe['materialization'][label],bindings,parameters)
                calls=_ordered_executable_refs(kernel)
                direct_bindings=_direct_operation_bindings(bindings,calls)
                composer.search_metrics['composition_expansions']+=len(calls)
                for child_id in calls:
                    composer.search_trace.append({'event':'COMPOSE_CHILD','structure_id':child_id,'materialization':label})
                node_replay={**replay,'operation_bindings':direct_bindings,'materialization':label}
                if label=='root':node_replay['materialized_step_id']=formed['step'].identity
                kernel['formation_recipe']=node_replay
                node_parents=tuple(dict.fromkeys((topology.identity,*calls)))
                definition={'operation_id':'candidate.'+label+'.'+digest({'topology':topology.identity,'parameters':parameters,'operations':selected})[:20],
                    'ports':(),'alpha_schema':AlphaSchema(('SEQUENCE','STATE','INDEX','BOOLEAN')),
                    'kernel':kernel,'metadata':{'candidate_role':'composed_step' if label=='step' else 'composed_operation',
                    'exec_capability':'composition' if label=='step' else 'composed_algorithm'}}
                if label=='step':
                    definition['metadata']['public_operation_interface']=recursive_step_operation_contract(definition['alpha_schema'].binding_slots)
                if label=='root':
                    definition['metadata']['public_operation_interface']=recursive_operation_contract(definition['alpha_schema'].binding_slots)
                obj=canonical_compose(node_parents,{'recipe':topology.identity,'bindings':parameters,'materialization':label},definition,levels,
                    source='generic_recipe_instantiation')
                levels[obj.identity]=obj.level;formed[label]=obj
                if label=='root':composer._interfaces[obj.identity]=normalize_operation(obj)
                cid='recipe-'+label+'-'+obj.identity
                if cid not in composer.kv.records:
                    composer.kv.add(cid,obj,operation_signature=obj.operation_id,formation_source='generic_recipe_instantiation',
                        formation_path=[{'topology_id':topology.identity,'materialization':label,
                            'parents_at_formation':list(node_parents),
                            'operation_bindings':direct_bindings}],task_signature=goal.signature(),goal_signature=goal.goal_signature,
                        slot_schema=obj.alpha_schema.binding_slots,binding_schema=names,candidate_graph=kernel,candidate_rank=rank,search_nodes=composer.nodes)
                composer.search_metrics['intermediate_materializations']+=1
            ids={'step_candidate_id':'recipe-step-'+formed['step'].identity,'step_structure_id':formed['step'].identity,
                 'root_candidate_id':'recipe-root-'+formed['root'].identity,'root_structure_id':formed['root'].identity}
            composer.search_trace.append({'event':'COMPOSITION_ATTEMPT','rank':rank,'topology_id':topology.identity,
                'bindings':parameters,'candidate_ids':ids,'search_nodes':composer.nodes,'search_depth':2})
            yield {'candidate_kind':'recipe_composition','rank':rank,'topology_id':topology.identity,
                'bindings':parameters,'candidate_ids':ids,'root_id':formed['root'].identity,'step_id':formed['step'].identity,
                'search_depth':len(path),'derivation_path':path}

    # Reusable, persisted F operations participate in the same typed operation
    # pool as any other public operation. Pair composition is deliberately
    # based only on public contracts; unpromoted KV candidates never become
    # children of another candidate.
    yield from public_binary_operation_candidates(composer,goal,start_rank=start_rank,
        rank=rank,excluded_ids=excluded_ids,lifecycle_view=lifecycle_view)


def public_binary_operation_candidates(composer,goal,*,start_rank=0,rank=0,excluded_ids=(),
                                      lifecycle_view='ACTIVE'):
    """Enumerate F(x) op G(x) over persisted, type-compatible public F edges."""
    from .serialization import digest

    excluded=set(excluded_ids)
    provider_pool=composer.operation_pool.get_operations(goal,lifecycle_view=lifecycle_view,
        operation_ids=tuple(composer._interfaces),excluded_ids=excluded)
    pool=sorted(provider_pool,
                key=lambda interface:(interface.identity,interface.operation_id))
    if not pool:return

    # The existing recursive token runtime already implements these typed
    # integer operators through its operator-map primitive. Keep this as a
    # finite grammar and filter by the public result type before enumeration.
    output_type=str(goal.output.get('type',''))
    typed_operators={'int':('+','-','*'),'float':('+','-','*')}
    operators=tuple(sorted(typed_operators.get(output_type,())))
    if not operators:return

    pairs=[]
    for left in pool:
        for right in pool:
            if left.identity==right.identity:continue
            if left.formal_slots!=right.formal_slots:continue
            if left.binding_schema!=right.binding_schema:continue
            if left.context_slots!=right.context_slots:continue
            if left.type_constraints!=right.type_constraints:continue
            if tuple((p.name,p.direction,p.dtype,p.cardinality) for p in left.input_ports)!=tuple(
                    (p.name,p.direction,p.dtype,p.cardinality) for p in right.input_ports):continue
            if tuple(p.dtype for p in left.output_ports)!=(output_type,):continue
            if tuple(p.dtype for p in right.output_ports)!=(output_type,):continue
            pairs.append((left,right))

    composer.search_trace.append({'event':'ENUMERATE_PUBLIC_OPERATION_PAIRS',
        'pool_ids':[item.identity for item in pool],
        'filtered_pair_count':len(pairs),'operators':list(operators),
        'library_only':True})
    levels=composer.operation_pool.levels_for(
        identity for pair in pairs for interface in pair for identity in (interface.identity,))
    for left,right in pairs:
        for operator in operators:
            rank+=1
            if rank<=start_rank:continue
            if rank<=composer.max_candidates:
                left_bindings=composer.bind_operation(left,goal)
                right_bindings=composer.bind_operation(right,goal)
                topology={'kind':'public_binary_operation_application','version':1,
                    'operator':operator,'operand_order':[left.identity,right.identity]}
                operation_id='operation.binary.'+digest({'left':left.operation_id,
                    'right':right.operation_id,'operator':operator})[:20]
                kernel={'executable':{'op':'binary_apply','operator':operator,
                    'left_f':left.identity,'right_f':right.identity}}
                left_contract=left.to_dict()
                public_contract={'protocol_version':left_contract['protocol_version'],
                    'kind':left_contract['kind'],'capability':left.capability_descriptor,
                    'ports':left_contract['ports'],'formal_slots':left_contract['formal_slots'],
                    'context_slots':left_contract['context_slots'],
                    'binding_schema':left_contract['binding_schema'],
                    'execution_entry':left_contract['execution_entry']}
                metadata={'exec_capability':'composed_algorithm',
                    'composition_rule':'public_binary_operation_application/1',
                    'public_operation_interface':public_contract}
                definition={'operation_id':operation_id,
                    'ports':tuple(Port(p.name,p.direction,p.dtype,p.cardinality) for p in left.ports),
                    'alpha_schema':AlphaSchema(left.formal_slots),
                    'kernel':kernel,'metadata':metadata}
                composer.search_trace.append({'event':'ENUMERATE_PUBLIC_OPERATION_PAIR',
                    'rank':rank,'left_id':left.identity,'right_id':right.identity,
                    'operator':operator,'search_depth':3,'public_contract_only':True})
                for child,role,bindings in ((left,'left',left_bindings),(right,'right',right_bindings)):
                    composer.search_trace.append({'event':'SELECT_OPERATION',
                        'structure_id':child.identity,'operation_id':child.operation_id,
                        'selected_as':'formation_child','role':role,'candidate_rank':rank})
                    composer.search_trace.append({'event':'BIND_PUBLIC_SLOTS',
                        'structure_id':child.identity,'bindings':bindings,
                        'formal_slots':list(child.formal_slots),
                        'selected_as':'formation_child','role':role,'candidate_rank':rank})
                    composer.search_trace.append({'event':'COMPOSE_CHILD',
                        'structure_id':child.identity,'role':role,
                        'candidate_rank':rank,'public_contract_only':True})
                obj=canonical_compose((left.identity,right.identity),topology,
                    definition,levels,source='public operation composition')
                composer._interfaces[obj.identity]=normalize_operation(obj)
                candidate_id='binary-'+obj.identity
                if candidate_id not in composer.kv.records:
                    composer.kv.add(candidate_id,obj,
                        operation_signature=obj.operation_id,
                        formation_source='public operation composition',
                        formation_path=[{'topology':topology,
                            'parents_at_formation':[left.identity,right.identity],
                            'operation_bindings':{'left':left.identity,'right':right.identity}}],
                        task_signature=goal.signature(),goal_signature=goal.goal_signature,
                        slot_schema=obj.alpha_schema.binding_slots,
                        binding_schema=[list(left.formal_slots)],
                        candidate_graph=kernel,candidate_rank=rank,
                        search_nodes=composer.nodes)
                composer.search_metrics['candidate_attempts']+=1
                composer.search_metrics['binding_combinations']+=1
                composer.search_metrics['composition_expansions']+=2
                composer.search_metrics['intermediate_materializations']+=1
                composer.nodes+=1
                composer.search_trace.append({'event':'MATERIALIZE_PUBLIC_OPERATION_PAIR',
                    'rank':rank,'candidate_id':candidate_id,'root_id':obj.identity,
                    'left_id':left.identity,'right_id':right.identity,'operator':operator,
                    'left_public_bindings':left_bindings,
                    'right_public_bindings':right_bindings,'search_depth':3})
                composer.search_trace.append({'event':'CANONICAL_COMPOSE',
                    'structure_id':obj.identity,'candidate_rank':rank,
                    'parents':[left.identity,right.identity],
                    'topology':topology,'search_depth':3})
                yield {'candidate_kind':'public_binary_operation_composition',
                    'rank':rank,'root_id':obj.identity,'step_id':None,
                    'topology_id':'public_binary_operation_application/1',
                    'operator':operator,'child_operation_ids':[left.identity,right.identity],
                    'child_bindings':{'left':left_bindings,'right':right_bindings},
                    'candidate_ids':{'root_candidate_id':candidate_id,
                        'root_structure_id':obj.identity},
                    'search_depth':3,
                    'derivation_path':({'kind':'public_operation_selection','structure_id':left.identity},
                        {'kind':'public_operation_selection','structure_id':right.identity},
                        {'kind':'binary_application','operator':operator})}
            else:
                composer.search_trace.append({'event':'SEARCH_EXPLOSION','limit':composer.max_candidates})
                return
