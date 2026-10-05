"""Strictly admit cross-Goal higher-order F from existing public operations."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'experiments')]
OUT=Path(os.environ.get('V94_HIGHER_ORDER_OUTPUT',ROOT/'artifacts/higher_order_composition'))
OUT.mkdir(parents=True,exist_ok=True)
NODE=r'C:\Users\lancelot\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'

from fsys.admission import (REQUIRED_TOKEN_CHECKS, audit_formation_parents,
                            subject_digest, validate_structure)
from fsys.autonomous_composition import AutonomousCompositionDiscovery
from fsys.external_kv import ExternalCandidateKV
from fsys.formation import canonical_compose
from fsys.goal_composer import GoalDrivenComposer, TaskSpec
from fsys.library import Library
from fsys.preference import UsageRegistry
from fsys.promotion import PromotionGate
from fsys.public_operations import is_first_class_operation, normalize_operation
from fsys.recursive_f_runtime import RecursiveFExecutor, program_snapshot
from fsys.serialization import digest, thaw
from fsys.token_goal_domain import publish_recursive_operation_contracts
from fsys.verifier import VerificationRecord
from synthesis_oracles import randomized_inputs

FIXED_EXAMPLES=[[],[3],[1,2,3,1,2],[5,4,3],[1,2,2,3],[-2,-1,0,2,-3],[0,0,0],[9,1,2,3,0]]
RUNTIME_CONTEXT={'STATE':{},'INDEX':None,'BOOLEAN':None}
CASES=(
    {'id':'HG01','earlier_id':'G01','earlier_name':'longest_increasing_run',
     'later_name':'increasing_run_plus_increase_count','seed':94101},
    {'id':'HG02','earlier_id':'G03','earlier_name':'longest_decreasing_run',
     'later_name':'decreasing_run_plus_equal_count','seed':94202},
)

def load_later_goals():
    data=json.loads((ROOT/'data/higher_order_goals.json').read_text(encoding='utf-8'))
    return {item['id']:item for item in data['later_goals']}

def make_goal(goal_id,name,fn,examples):
    signature=digest({'goal_family':'sequence-int-to-int','input':'sequence<int>','output':'int'})
    rows=tuple({'input':[xs],'output':fn(xs)} for xs in examples)
    return TaskSpec(goal_id,({'name':'nums','type':'sequence<int>'},),{'type':'int'},rows,signature)

def js_batch(program,inputs):
    runtime=(ROOT/'fsys/recursive_f_runtime.js').read_text(encoding='utf-8')
    code=runtime+'\nconst P='+json.dumps(program,separators=(',',':'))+';const R=makeFExecutor(P);console.log(JSON.stringify('+json.dumps(inputs,separators=(',',':'))+'.map(x=>R.run([x]).result)));'
    with tempfile.TemporaryDirectory() as td:
        path=Path(td)/'batch.js';path.write_text(code,encoding='utf-8')
        proc=subprocess.run([NODE,str(path)],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',check=True)
    return json.loads(proc.stdout.strip().splitlines()[-1])

def search_match(library,source_name,source,fn,goal_id,*,seed,excluded_ids=(),random_count=128,
                 usage_registry=None):
    examples=FIXED_EXAMPLES
    random_x=randomized_inputs(random_count,seed)
    random_expected=[fn(xs) for xs in random_x]
    goal=make_goal(goal_id,source_name,fn,examples)
    kv=ExternalCandidateKV()
    composer=GoalDrivenComposer(library,kv,public_contracts=publish_recursive_operation_contracts(library),
                                usage_registry=usage_registry)
    attempts=[];winner=None
    for candidate in composer.candidates(goal,excluded_ids=excluded_ids):
        item={'kind':candidate['candidate_kind'],'rank':candidate.get('rank'),
              'root_id':candidate['root_id'],'search_depth':candidate['search_depth'],
              'child_operation_ids':candidate.get('child_operation_ids'),
              'operator':candidate.get('operator')}
        try:
            actual=[composer.execute_candidate(candidate,goal,[xs],runtime_context=RUNTIME_CONTEXT) for xs in examples]
            item['examples_pass']=actual==[fn(xs) for xs in examples]
            if item['examples_pass']:
                random_actual=[composer.execute_candidate(candidate,goal,[xs],runtime_context=RUNTIME_CONTEXT) for xs in random_x]
                item['randomized_oracle_pass']=random_actual==random_expected
            else:item['randomized_oracle_pass']=False
        except (ValueError,KeyError,TypeError,IndexError,LookupError,RuntimeError) as error:
            item['examples_pass']=False;item['randomized_oracle_pass']=False
            item['error']=type(error).__name__+': '+str(error)
        attempts.append(item)
        if item['examples_pass'] and item['randomized_oracle_pass']:
            winner=candidate;break
        for cid in candidate.get('candidate_ids',{}).values():
            if cid in kv.records and kv.get(cid)['status']=='CANDIDATE':
                kv.transition(cid,'REJECTED',reason='fixed examples or fixed-seed oracle mismatch')
    return {'candidate':winner,'composer':composer,'kv':kv,'goal':goal,'examples':examples,
        'random_inputs':random_x,'oracle_outputs':random_expected,'attempts':attempts,
        'metrics':dict(composer.search_metrics),'source_name':source_name,'source':source,'fn':fn,
        'library':library,'goal_id':goal_id,'usage_registry':usage_registry}

def candidate_object(result):
    candidate=result['candidate']
    cid=candidate.get('candidate_ids',{}).get('root_candidate_id')
    if cid and cid in result['kv'].records:
        from fsys.structure import Structure
        return cid,Structure.from_dict(result['kv'].get(cid)['structure'])
    return None,result['library'][candidate['root_id']]

def executable_audit(obj,library):
    exe=thaw(obj.kernel).get('executable',{})
    if exe.get('op')=='binary_apply':
        children=(exe.get('left_f'),exe.get('right_f'))
        if tuple(children)!=tuple(obj.parents):return False
        if any(child not in library._structures for child in children):return False
        contracts=[normalize_operation(library[child]) for child in children]
        if any(not is_first_class_operation(contract) for contract in contracts):return False
        return (contracts[0].type_constraints==contracts[1].type_constraints
            and contracts[0].binding_schema==contracts[1].binding_schema
            and contracts[0].context_slots==contracts[1].context_slots)
    return False

def cold_execute(library,kv,root_id,inputs,expected,tag):
    with tempfile.TemporaryDirectory() as td:
        folder=Path(td);library_path=folder/'library.json';kv_path=folder/'kv.json';request_path=folder/'request.json'
        library.save(library_path);kv.save(kv_path)
        request_path.write_text(json.dumps({'library':str(library_path),'kv':str(kv_path),
            'root':root_id,'inputs':inputs},separators=(',',':')),encoding='utf-8')
        code=('import json,sys;from pathlib import Path;from fsys.library import Library;'
            'from fsys.external_kv import ExternalCandidateKV;from fsys.recursive_f_runtime import RecursiveFExecutor;'
            'd=json.loads(Path(sys.argv[1]).read_text());l=Library.load(d["library"]);k=ExternalCandidateKV.load(d["kv"]);'
            'e=RecursiveFExecutor(l,k);print(json.dumps([e.execute(d["root"],[x]) for x in d["inputs"]]))')
        proc=subprocess.run([sys.executable,'-c',code,str(request_path)],cwd=ROOT,
            capture_output=True,text=True,encoding='utf-8',check=True)
        actual=json.loads(proc.stdout.strip().splitlines()[-1])
    if actual!=expected:raise AssertionError(tag+' cold process disagrees with oracle')
    return actual

def canonical_recurrence(results):
    rows=[]
    for result in results:
        cid,obj=candidate_object(result)
        rows.append({'success':True,'candidate_id':cid,'structure':obj,
            'compositions':[{'children':list(obj.parents),
                'topology':thaw(obj.kernel).get('composition_topology',{})}]})
    proposals=AutonomousCompositionDiscovery().discover(rows)
    if not proposals:raise AssertionError('independent successful formations did not recur')
    chosen=rows[0]['structure'];proposal=next((p for p in proposals if tuple(p['children'])==chosen.parents),None)
    if proposal is None:raise AssertionError('candidate child graph missing from recurrence proposals')
    definition={'operation_id':chosen.operation_id,'ports':chosen.ports,
        'alpha_schema':chosen.alpha_schema,'kernel':thaw(chosen.kernel),'metadata':thaw(chosen.metadata)}
    levels={s.identity:s.level for s in results[0]['library']}
    formed=canonical_compose(proposal['children'],proposal['topology'],definition,levels,
        source='autonomous discovery',evidence={'independent_successful_searches':len(results)})
    if formed.identity!=chosen.identity:raise AssertionError('canonical recurrence changed operation identity')
    return formed,proposal

def promote_with_full_gate(result,independent_results,*,label,final_library_path=None,
                           usage_registry=None):
    library=result['library'];kv=result['kv'];candidate=result['candidate']
    cid,obj=candidate_object(result)
    if cid is None:raise AssertionError('higher-order admission requires a formed KV candidate')
    formed,proposal=canonical_recurrence(independent_results)
    if formed.identity!=obj.identity:raise AssertionError('independent formation IDs differ')
    # Candidate-bound execution and validation evidence.
    examples=result['examples'];random_x=result['random_inputs'];fn=result['fn'];goal=result['goal']
    python_values=[result['composer'].execute_candidate(candidate,goal,[xs],runtime_context=RUNTIME_CONTEXT)
        for xs in random_x]
    oracle=result['oracle_outputs']
    program=program_snapshot(library,obj.identity,kv)
    js_values=js_batch(program,random_x)
    if python_values!=oracle or js_values!=oracle:raise AssertionError(label+' Python/JS/oracle mismatch')
    example_values=[result['composer'].execute_candidate(candidate,goal,[xs],runtime_context=RUNTIME_CONTEXT)
        for xs in examples]
    if example_values!=[fn(xs) for xs in examples]:raise AssertionError(label+' supplied example mismatch')
    tracer=RecursiveFExecutor(library,kv,trace=True)
    traced_values=[tracer.execute(obj.identity,[xs]) for xs in random_x]
    if traced_values!=oracle:raise AssertionError(label+' traced execution mismatch')
    checks={
        'interface':is_first_class_operation(normalize_operation(obj)),
        'domain_audit':executable_audit(obj,library),
        'formation_parents':audit_formation_parents(formed,library,
            [{'children':list(proposal['children']),'topology':proposal['topology']}])['passed'],
        'executable':obj.identity in [event['structure_id'] for event in tracer.dispatches],
        'examples':example_values==[fn(xs) for xs in examples],
        'randomized_oracle':python_values==oracle,
        'cross_language':js_values==oracle,
        'cold_instance':True,
        'x_leakage':True}
    serialized=json.dumps(obj.to_dict(),ensure_ascii=False,sort_keys=True)
    checks['x_leakage']=result['source'] not in serialized and all(
        json.dumps(xs,separators=(',',':')) not in serialized for xs in examples if xs)
    # Run the required pre-promotion cold validation in a separate process.
    cold= cold_execute(library,kv,obj.identity,random_x,oracle,label+' pre-promotion')
    checks['cold_instance']=cold==oracle
    validate_structure(formed)
    if not all(checks.get(key) is True for key in REQUIRED_TOKEN_CHECKS):
        raise AssertionError(label+' admission checks failed: '+repr(checks))
    verification_digest=digest({'candidate':obj.identity,'checks':checks,
        'oracle':oracle,'javascript':js_values,'cold':cold})
    from fsys.admission import subject_digest
    base_receipt={'passed':True,'verifier':'fixed-seed Python/F/JavaScript/oracle + cold process',
        'receipt_digest':verification_digest,'admission_checks':checks,
        'admission_subject_digest':subject_digest(formed)}
    # Keep separate candidate-bound records for randomized/cross-language and cold checks.
    kv.update(cid,structure=formed.to_dict(),formation_source='autonomous discovery',
        formation_path=[{'children':list(proposal['children']),'topology':proposal['topology']}],
        independent_formation_count=len(independent_results),x_leakage_status='ZERO')
    kv.add_receipt(cid,{**base_receipt,'cases':len(random_x),'validation_stage':'randomized_oracle'})
    kv.add_receipt(cid,{**base_receipt,'cases':len(random_x),'validation_stage':'cold_instance'})
    kv.transition(cid,'VALIDATED',reason='full V9.4 token admission checks passed')
    usage=usage_registry or UsageRegistry(library);gate=PromotionGate(library,kv,usage)
    persisted=gate.promote(cid,operation_identity=formed.operation_id,
        min_verified_cases=len(random_x),min_cold_instance_cases=len(random_x),
        independent_formations=len(independent_results),x_leakage_status='ZERO',executable=True,
        reason='public typed operation composition passed full admission')
    if persisted.identity!=formed.identity:raise AssertionError('promotion changed F identity')
    if final_library_path:library.save(final_library_path)
    return {'candidate_id':cid,'formed':formed,'persisted':persisted,'checks':checks,
        'receipts':gate.receipts,'trace_dispatches':tracer.dispatches,
        'source_oracle':oracle,'python_values':python_values,'javascript_values':js_values,
        'cold_values':cold,'search_trace':result['composer'].search_trace,
        'canonical_proposal':proposal,'library':library,'kv':kv}


def main_clean():
    """Rerun the two existing higher-order Goals against the strict clean Library."""
    library_path=Path(os.environ.get('V94_CLEAN_HIGHER_ORDER_LIBRARY',
        ROOT/'artifacts/clean_strict_library/library_final.json'))
    usage_path=Path(os.environ.get('V94_CLEAN_HIGHER_ORDER_USAGE',
        ROOT/'artifacts/clean_strict_library/usage_final.json'))
    summary_path=Path(os.environ.get('V94_CLEAN_HIGHER_ORDER_SUMMARY',
        ROOT/'artifacts/clean_strict_library/summary.json'))
    summary=json.loads(summary_path.read_text(encoding='utf-8'))
    root_map=summary['goal_root_map']
    selected={item.strip() for item in os.environ.get('V94_HIGHER_ORDER_GOALS','HG01,HG02').split(',') if item.strip()}
    cases=[]
    for original in CASES:
        if original['id'] not in selected:continue
        case=dict(original)
        case['earlier_f']=root_map[case['earlier_id']]
        other_goal='G05' if case['id']=='HG01' else 'G07'
        case['other_f']=root_map[other_goal]
        case['other_goal_id']=other_goal
        cases.append(case)
    if {item['id'] for item in cases}!=selected:
        raise ValueError('unknown higher-order Goal ID: '+', '.join(sorted(selected-{item['id'] for item in cases})))
    OUT.mkdir(parents=True,exist_ok=True)
    later_goals=load_later_goals()
    outputs=[]
    for case in cases:
        initial=Library.load(library_path)
        initial_hash=hashlib.sha256(library_path.read_bytes()).hexdigest()
        goal=later_goals[case['id']]
        namespace={};exec(goal['source'],namespace,namespace)
        fn=namespace[goal['later_function']]
        calls=sorted({node.func.id for node in ast.walk(ast.parse(goal['source']))
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name)})
        if case['earlier_name'] in calls:
            raise AssertionError(case['id']+' source forwards to earlier Goal')
        for child in (case['earlier_f'],case['other_f']):
            if child not in initial._structures or not is_first_class_operation(
                    normalize_operation(initial[child],publish_recursive_operation_contracts(initial).get(child))):
                raise AssertionError(case['id']+' selected child is not a persisted public operation: '+child)
        usage=UsageRegistry.load(usage_path,initial)
        first=search_match(initial,goal['later_function'],goal['source'],fn,case['id'],
            seed=case['seed'],usage_registry=usage)
        second_library=Library.load(library_path)
        second_usage=UsageRegistry.load(usage_path,second_library)
        second=search_match(second_library,goal['later_function'],goal['source'],fn,case['id'],
            seed=case['seed']+1,usage_registry=second_usage)
        if not first['candidate'] or not second['candidate']:
            raise AssertionError(case['id']+' failed clean higher-order acquisition')
        if first['candidate']['candidate_kind']!='public_binary_operation_composition':
            raise AssertionError(case['id']+' did not select a public binary F edge')
        _,formed_obj=candidate_object(first)
        if set(formed_obj.parents)!={case['earlier_f'],case['other_f']}:
            raise AssertionError(case['id']+' formation parents differ from selected public operations')
        if candidate_object(second)[1].identity!=formed_obj.identity:
            raise AssertionError(case['id']+' independent formation changed canonical identity')
        promoted_path=OUT/(case['id']+'_library.json')
        promoted=promote_with_full_gate(first,[first,second],label=case['id']+' clean strict',
            final_library_path=promoted_path,usage_registry=usage)
        if hashlib.sha256(library_path.read_bytes()).hexdigest()!=initial_hash:
            raise AssertionError('clean strict baseline Library was modified')
        usage_path_out=OUT/(case['id']+'_usage.json')
        usage_path_out.write_text(json.dumps(usage.snapshot(),ensure_ascii=False,indent=2),encoding='utf-8')
        cold_library=Library.load(promoted_path)
        cold_usage=UsageRegistry.load(usage_path_out,cold_library)
        cold_composer=GoalDrivenComposer(cold_library,ExternalCandidateKV(),
            public_contracts=publish_recursive_operation_contracts(cold_library),usage_registry=cold_usage)
        direct=next(item for item in cold_composer.candidates(first['goal'])
            if item['candidate_kind']=='operation_edge' and item['root_id']==promoted['persisted'].identity)
        cold_value=cold_composer.execute_candidate(direct,first['goal'],[FIXED_EXAMPLES[2]],runtime_context=RUNTIME_CONTEXT)
        expected=fn(FIXED_EXAMPLES[2])
        if cold_value!=expected:
            raise AssertionError(case['id']+' cold-reloaded public operation disagrees with oracle')
        cold_values=cold_execute(cold_library,promoted['kv'],promoted['persisted'].identity,
            first['random_inputs'],first['oracle_outputs'],case['id']+' cold reload')
        if cold_values!=first['oracle_outputs']:
            raise AssertionError(case['id']+' separate-process cold reload mismatch')
        child_ids=set(first['candidate']['child_operation_ids'])
        root_id=first['candidate']['root_id'];winning_rank=first['candidate']['rank']
        binding_trace=[];seen_trace=set()
        for event in first['composer'].search_trace:
            kind=event['event'];sid=event.get('structure_id')
            keep=(
                (kind=='RETRIEVE_OPERATION' and sid in child_ids)
                or kind=='ENUMERATE_PUBLIC_OPERATION_PAIRS'
                or (kind=='ENUMERATE_PUBLIC_OPERATION_PAIR' and event.get('rank')==winning_rank)
                or (kind=='MATERIALIZE_PUBLIC_OPERATION_PAIR' and event.get('root_id')==root_id)
                or (kind=='COMPOSE_CHILD' and event.get('candidate_rank')==winning_rank)
                or (kind=='CANONICAL_COMPOSE' and sid==root_id)
                or (kind in ('SELECT_OPERATION','BIND_PUBLIC_SLOTS') and sid==root_id)
                or (kind in ('SELECT_OPERATION','BIND_PUBLIC_SLOTS')
                    and sid in child_ids and event.get('selected_as')=='child'
                    and event.get('parent_id')==root_id)
                or (kind=='EXECUTE_OPERATION' and sid==root_id)
                or (kind=='USE_AS_CHILD_OPERATION' and event.get('parent_id')==root_id
                    and sid in child_ids))
            if not keep:continue
            key=(kind,sid,event.get('root_id'),event.get('parent_id'),event.get('role'),
                event.get('selected_as'),event.get('rank',event.get('candidate_rank')))
            if key in seen_trace:continue
            seen_trace.add(key);binding_trace.append(event)
        output={
            'goal':case['id'],'goal_function':goal['later_function'],
            'library_sha256_before':initial_hash,
            'library_structures_before':len(initial),
            'selected_children':{'earlier_goal_F':case['earlier_f'],
                'other_goal_F':case['other_f']},
            'candidate_kind':first['candidate']['candidate_kind'],
            'candidate_rank':first['candidate']['rank'],
            'search_depth':first['candidate']['search_depth'],
            'result_F':promoted['persisted'].identity,
            'parents':list(promoted['persisted'].parents),
            'exact_parent_audit':promoted['checks']['formation_parents'],
            'admission_checks':promoted['checks'],
            'metrics':first['metrics'],
            'independent_candidate_rank':second['candidate']['rank'],
            'independent_identity_equal':candidate_object(second)[1].identity==formed_obj.identity,
            'oracle_python_javascript_equal':first['oracle_outputs']==promoted['python_values']==promoted['javascript_values'],
            'public_cold_reload_output':cold_value,
            'cold_process_cases':len(cold_values),
            'cold_process_equal_oracle':cold_values==first['oracle_outputs'],
            'search_trace':binding_trace,
        }
        outputs.append(output)
    results={'status':'PASS','source_library':str(library_path.relative_to(ROOT)),
        'source_library_sha256':hashlib.sha256(library_path.read_bytes()).hexdigest(),
        'goals':outputs,'baseline_unchanged':True}
    (OUT/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    report=['# V9.4 clean strict higher-order F composition','',
        '**Result: PASS — higher-order composition, exact parents, admission and cold reload.**','',
        f"Baseline: `{len(Library.load(library_path))}` Structures; SHA-256 `{results['source_library_sha256']}`. The baseline file was not modified.",'',
        'Each Goal was searched twice from the same clean strict Library and UsageRegistry snapshot. The Composer enumerated public binary operation pairs, then selected and bound only the two public operations. The accepted F parents exactly equal those selected persisted F IDs. Candidate admission, Python/F/JavaScript/oracle checks, exact-parent audit, promotion, public retrieval after cold load, and separate-process cold execution all passed.','',
        '| Goal | Selected F children | New F | attempts | bindings | depth | exact parents | semantic equality | cold reload |',
        '|---|---|---|---:|---:|---:|---|---|---|']
    for row in outputs:
        report.append(f"| {row['goal']} | `{row['selected_children']['earlier_goal_F']}` + `{row['selected_children']['other_goal_F']}` | `{row['result_F']}` | {row['metrics']['candidate_attempts']} | {row['metrics']['binding_combinations']} | {row['search_depth']} | PASS | PASS | PASS ({row['cold_process_cases']} cases) |")
    report.append('')
    for row in outputs:
        report.extend(['',f"### {row['goal']} trace",'',
        '```text',
        f"RETRIEVE public child F: {row['selected_children']['earlier_goal_F']}",
        f"RETRIEVE public child F: {row['selected_children']['other_goal_F']}",
        f"SELECT public binary operation pair at rank {row['candidate_rank']} (search depth {row['search_depth']})",
            f"BIND only public slots for both children",
            f"COMPOSE and admit {row['result_F']}",
            f"exact formation parents: {row['parents']}",
            '```',''])
    report.extend(['## Admission checks','',
        'Every new F passed interface, public-operation/domain compatibility, execution, examples, fixed-seed oracle, JavaScript equality, separate-process cold execution, source/input leakage, and exact formation-parent validation. Promotion persisted the same canonical identity. The two acquisitions used clean-room Library copies; the official clean strict baseline remained unchanged.',''])
    (OUT/'report.md').write_text('\n'.join(report),encoding='utf-8')
    print(json.dumps({'status':'PASS','goals':len(outputs),
        'results':[{'goal':x['goal'],'F':x['result_F'],'attempts':x['metrics']['candidate_attempts'],
            'bindings':x['metrics']['binding_combinations'],'depth':x['search_depth']} for x in outputs]},ensure_ascii=False))


if __name__=='__main__':
    main_clean()
