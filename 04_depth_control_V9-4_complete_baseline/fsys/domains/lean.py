"""Lean Prop syntax, proof-state rules and certificates. Lifecycle lives in fsys core."""
import itertools,json,time,re
from dataclasses import replace
from pathlib import Path
from ..domain import DomainAdapter,StructuralMatch,Action,Artifact
from ..structure import Leaf
from ..ports import Port
from ..alpha import AlphaSchema,AlphaTensor
from ..library import Library
from ..provenance import FormationProvenance
from ..formation import canonical_compose
from ..verifier import LeanVerifier,VerificationRecord
from ..backend import NumericFeatureBatch
from ..serialization import FrozenMap,digest,thaw
from ._lean import proof as p
from ._lean import matching
from ._lean.ast import Prop,Atom,Imp,And,Slot,parse

PORTS=(Port('state_in','input','proof_state','context_and_goals'),Port('state_out','output','proof_state','context_and_goals'))
VIEWS={'f_imp_intro':Imp(Slot('A'),Slot('B')),'f_exact':Slot('G'),'f_imp_apply':Slot('G'),
       'f_and_construct':And(Slot('A'),Slot('B')),'f_and_cases':Slot('G')}
def fixture():return json.loads((Path(__file__).parent/'_lean/fixtures.json').read_text(encoding='utf-8'))
def skeleton(a,b):
    return a.op=='Slot' or a.op==b.op and (a==b if a.op=='Atom' else all(skeleton(x,y) for x,y in zip(a.args,b.args)))

class RuleView:
    """Ephemeral schema facade for stateless proof rules; no legacy library lifecycle."""
    def __init__(self,domain,library,extra=()):
        self.domain=domain;self.library=library;self.items={s.identity:s for s in itertools.chain(library,extra)}
        self.schemas={sid:domain.schema(s) for sid,s in self.items.items() if s.parents and s.kernel.get('reference')!='lean.state_sequence'}
    def get_level(self,sid):return self.items[sid].level
    def get_recursive_primitives(self,sid):
        s=self.items[sid]
        return [sid] if not s.parents else sum((self.get_recursive_primitives(i) for i in s.parents),[])

class LeanPropDomain(DomainAdapter):
    name='lean_prop'
    bootstrap_accepts_verifier=True
    def __init__(self):self._schema_cache={};self._key_cache={}
    def validate_instance(self,X):
        if isinstance(X,str):
            try:X=parse(X)
            except IndexError as error:raise ValueError('incomplete propositional expression') from error
        def valid(e):
            if not isinstance(e,Prop):return False
            if e.op=='Atom':return len(e.args)==1 and isinstance(e.args[0],str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',e.args[0]) is not None
            return e.op in ('Imp','And') and len(e.args)==2 and all(valid(a) for a in e.args)
        if not valid(X):raise ValueError('expected closed propositional theorem')
        return X
    def state_type(self,X):return 'proof_state'
    def candidate_space(self,X):return tuple(dict.fromkeys(p.walk(X)))
    def port_compatible(self,a,b):return a.direction=='output' and b.direction=='input' and a.dtype==b.dtype and a.cardinality==b.cardinality
    def schema(self,s):
        # Cache contains abstract schemas only; no current X.
        key=(s.identity,s.metadata.get('schema_json'))
        if key not in self._schema_cache:self._schema_cache[key]=p.fromdata(json.loads(s.metadata['schema_json'])) if s.parents else VIEWS[s.identity]
        return self._schema_cache[key]
    def structural_signature(self,s):
        if s.kernel.get('reference')=='lean.state_sequence':return {'root_operator':'state_sequence','node_count':len(s.parents),'slots':len(s.alpha_schema.binding_slots),'level':s.level}
        e=self.schema(s)
        return {'root_operator':e.op,'node_count':sum(1 for _ in p.walk(e)),'slots':len(s.alpha_schema.binding_slots),'level':s.level}
    def retrieval_candidates(self,library):return [s for s in library if s.parents and s.kernel.get('reference')!='lean.state_sequence']
    def prefilter(self,structures,X):return [s for s in structures if skeleton(self.schema(s),X)]
    def structural_exact(self,s,X,context=None):
        if s.kernel.get('reference')=='lean.state_sequence':
            if context is None:return StructuralMatch(False,{})
            try:
                trial=context.engine.context(X);trial.current_state=context.current_state
                binding=self._run_sequence(s,X,trial,derive=True)
                return StructuralMatch(True,binding)
            except (ValueError,KeyError):return StructuralMatch(False,{})
        a=p.unify(self.schema(s),X) if s.parents else None
        if not s.parents and context is not None:
            state=context.current_state or self.initial_state(X)
            a=p.unify(self.schema(s),state[0][1]) if state else None
            if a is not None and not any(action.structure_id==s.identity for action in self.search_actions(state,context)):a=None
        return StructuralMatch(a is not None,a or {},score=float(a is not None))
    def structural_partial(self,s,X,context=None,score=None):
        e=self.schema(s)
        if e.op=='Slot':a={e.args[0]:X};info={'score':1.}
        else:info,a=matching.matching(e,X)
        return {'id':s.identity,'alpha':{k:p.d(v) for k,v in a.items()},'score':info['score'] if score is None else score,
                'mismatch_frontier':p.frontier(e,X,a)}
    def encode_binding(self,s,X,binding,solver='direct_structural'):
        candidates=self.candidate_space(X);values=[]
        for name in s.alpha_schema.binding_slots:
            index=candidates.index(binding[name]);values.extend(4. if j==index else -4. for j in range(len(candidates)))
        return AlphaTensor(tuple(values),(True,)*len(values),(len(s.alpha_schema.binding_slots),len(candidates)),solver)
    def decode_alpha(self,s,X,alpha):
        candidates=self.candidate_space(X);k=len(candidates);names=s.alpha_schema.binding_slots
        if alpha.shape!=(len(names),k):raise ValueError('alpha does not match dynamic candidate space')
        out={}
        for i,name in enumerate(names):
            ids=[j for j in range(k) if alpha.mask[i*k+j]]
            if not ids:raise ValueError('empty alpha row')
            out[name]=candidates[max(ids,key=lambda j:alpha.values[i*k+j])]
        return out
    def binding_legal(self,s,X,binding):
        if s.kernel.get('reference')=='lean.state_sequence':return set(binding)==set(s.alpha_schema.binding_slots)
        try:return p.substitute(self.schema(s),binding)==X
        except KeyError:return False
    def alpha_from_batch(self,s,X,values,batch,solver='continuous_adam'):
        count=len(s.alpha_schema.binding_slots)*self.candidate_count(X)
        return AlphaTensor(tuple(values[:count]),(True,)*count,(len(s.alpha_schema.binding_slots),self.candidate_count(X)),solver)
    def prepare_numeric_batch(self,structures,X,context=None):
        import numpy as np
        st=time.perf_counter();candidates=self.candidate_space(X);ix={e:i for i,e in enumerate(candidates)}
        slots=[s.alpha_schema.binding_slots for s in structures];n=len(slots);smax=max(map(len,slots));k=len(candidates)
        occ=np.zeros((n,smax,k),np.float32);cov=np.zeros_like(occ);mask=np.zeros_like(occ,dtype=bool);op=np.zeros(n,np.float32)
        sizes={e:sum(1 for _ in p.walk(e)) for e in candidates}
        for i,(s,names) in enumerate(zip(structures,slots)):
            ni={name:j for j,name in enumerate(names)};correct=total=0;mask[i,:len(names)]=True
            def align(a,b):
                nonlocal correct,total
                total+=1
                if a.op=='Slot':occ[i,ni[a.args[0]],ix[b]]+=1;cov[i,ni[a.args[0]],ix[b]]+=sizes[b];return
                if a.op==b.op:
                    correct+=1
                    if a.op in ('Imp','And'):
                        for aa,bb in zip(a.args,b.args):align(aa,bb)
            align(self.schema(s),X);op[i]=correct/max(1,total)
        counts=occ.sum(-1,keepdims=True);q=occ/np.maximum(counts,1);cq=cov/np.maximum(cov.sum((-1,-2),keepdims=True),1)
        packed=np.concatenate((q.reshape(n,-1),cq.reshape(n,-1),(occ.sum(-1)>1).astype(np.float32),op[:,None]),axis=1)
        return NumericFeatureBatch(packed,mask,(n,smax,k),time.perf_counter()-st,tuple(s.identity for s in structures))
    def default_verifier(self):return LeanVerifier()
    def audit_structure(self,s):
        if not s.parents:return s.identity in VIEWS
        if s.kernel.get('reference')=='lean.state_sequence':
            return (s.operation_id=='lean.state_sequence' and tuple(a['id'] for a in s.metadata['call_plan'])==s.parents
                    and s.alpha_schema.binding_slots==tuple(f'p{i}_{name}' for i,group in enumerate(s.metadata['slot_groups']) for name in group))
        try:return (all(n.op in ('Slot','Imp','And') for n in p.walk(self.schema(s)))
                    and tuple(p.slotnames(self.schema(s)))==s.alpha_schema.binding_slots
                    and tuple(a['id'] for a in s.metadata['call_plan'])==s.parents and 'proof_source' in s.metadata)
        except (KeyError,ValueError):return False
    def _helpers(self,context,ids,extra=()):
        view=RuleView(self,context.library,extra);needed=set()
        def visit(sid):
            if sid in needed:return
            s=view.items[sid]
            for pid in s.parents:visit(pid)
            if s.parents:needed.add(sid)
        for sid in ids:visit(sid)
        return '\n\n'.join(view.items[sid].metadata['proof_source'] for sid in sorted(needed,key=lambda i:(view.get_level(i),i)))+'\n'
    def execute_composite(self,s,X,alpha,context):
        if s.kernel.get('reference')=='lean.state_sequence':
            binding=self.decode_alpha(s,X,alpha);outer=not getattr(context,'transition_mode',False)
            self._run_sequence(s,X,context,binding=binding)
            if outer and not context.current_state:
                from ..search import PlanResult
                plan=PlanResult(True,tuple(context.transition_actions),'SUCCESS',{})
                artifact=self.artifact_from_plan(plan,context);artifact.value=context.current_state;return artifact
            return Artifact(context.current_state)
        if getattr(context,'transition_mode',False):
            binding=self.decode_alpha(s,X,alpha);ctx,g=context.current_state[0]
            legal=[a for a in self._calls(s,ctx,g,context) if a['alpha']==binding]
            if not legal:raise ValueError('whole-F alpha is not legal in this proof state')
            action=Action(s.identity,legal[0]);context.current_state=self.step(context.current_state,action,context)
            context.transition_actions.append(action);return Artifact(context.current_state)
        binding=self.decode_alpha(s,X,alpha)
        if not self.binding_legal(s,X,binding):raise ValueError('decoded alpha is not a legal instantiation')
        view=RuleView(self,context.library)
        return Artifact(s.identity,self._helpers(context,(s.identity,))+p.exact_proof(view,s.identity,binding,X,'fsys_reuse'))
    def execute_leaf(self,s,X,alpha,context):
        state=context.current_state or self.initial_state(X)
        bound=self.decode_alpha(s,X,alpha)
        if not state or p.substitute(self.schema(s),bound)!=state[0][1]:raise ValueError('primitive alpha does not instantiate this goal')
        legal=[a for a in self.search_actions(state,context) if a.structure_id==s.identity]
        if not legal:raise ValueError('primitive is not legal in this proof state')
        # Hypothesis selection is an explicit runtime routing input when ambiguous.
        chosen=context.temporary_substitution.get('action',legal[0])
        if chosen not in legal:raise ValueError('illegal primitive routing')
        context.current_state=self.step(state,chosen,context)
        if getattr(context,'transition_mode',False):context.transition_actions.append(chosen)
        return Artifact(context.current_state)
    def composition_definition(self,left,right,wiring):
        if tuple(tuple(edge) for edge in wiring)!=(('state_out','state_in'),):raise ValueError('Lean composition supports sequential proof-state wiring')
        groups=(left.alpha_schema.binding_slots,right.alpha_schema.binding_slots)
        return {'operation_id':'lean.state_sequence','ports':PORTS,
                'alpha_schema':AlphaSchema(tuple(f'p{i}_{name}' for i,group in enumerate(groups) for name in group)),
                'kernel':{'reference':'lean.state_sequence'},'metadata':{'slot_groups':groups,'wiring':wiring,
                'call_plan':[{'kind':'STRUCTURE','id':left.identity},{'kind':'STRUCTURE','id':right.identity}]}}
    def _run_sequence(self,s,X,context,binding=None,derive=False):
        """Instantiate a declared sequence; never searches for a completion path."""
        context.current_state=context.current_state if context.current_state is not None else self.initial_state(X)
        outer=not getattr(context,'transition_mode',False)
        if outer:context.transition_actions=[]
        previous=getattr(context,'transition_mode',False);context.transition_mode=True;combined={}
        try:
            for i,pid in enumerate(s.parents):
                if not context.current_state:raise ValueError('sequence calls an operator after the proof is closed')
                child=context.library[pid];names=child.alpha_schema.binding_slots
                if derive:
                    if child.kernel.get('reference')=='lean.state_sequence':
                        child_binding=self._run_sequence(child,X,context,derive=True)
                        combined.update({f'p{i}_{name}':child_binding[name] for name in names});continue
                    ctx,g=context.current_state[0]
                    if child.parents:
                        actions=self._calls(child,ctx,g,context)
                        if not actions:raise ValueError('whole-F has no legal proof-state instantiation')
                        child_binding=actions[0]['alpha']
                    else:
                        child_binding=p.unify(self.schema(child),g)
                        if child_binding is None:raise ValueError('primitive goal mismatch')
                else:child_binding={name:binding[f'p{i}_{name}'] for name in names}
                combined.update({f'p{i}_{name}':child_binding[name] for name in names})
                if not child.parents:
                    legal=[a for a in self.search_actions(context.current_state,context) if a.structure_id==pid]
                    if not legal:raise ValueError('primitive is not legal in composed proof state')
                    context.temporary_substitution['action']=legal[0]
                alpha=self.encode_binding(child,X,child_binding)
                child.execute(X,alpha,context)
        finally:context.transition_mode=previous
        return combined
    def initial_state(self,X):return (((),X),)
    def state_key(self,state):return state
    def is_complete(self,state,context):return not state
    def _ids(self,state,context):
        ids=context.engine.index.query(state[0]) if context.index_enabled else tuple(s.identity for s in self.retrieval_candidates(context.library))
        if context.search_scope is not None:ids=tuple(i for i in ids if i in context.search_scope)
        return tuple(context.library[i] for i in ids)
    def _calls(self,s,ctx,g,context):
        context.count('candidate_probes')
        key=(s.identity,ctx,g)
        if context.cache_enabled and key in context.compatibility_cache:context.count('cache_hits');return context.compatibility_cache[key]
        if not hasattr(context,'rule_view'):context.rule_view=RuleView(self,context.library)
        def counted(*args):
            context.count('structural_checks');return p.unify(*args)
        out=p.call_actions(context.rule_view,s.identity,ctx,g,counted)
        if context.cache_enabled:context.compatibility_cache[key]=out
        return out
    def search_actions(self,state,context):
        ctx,g=state[0];out=[];known={s.identity for s in context.library if not s.parents}
        if context.search_scope is not None:known.intersection_update(context.search_scope)
        if 'f_imp_intro' in known and g.op=='Imp':out.append({'kind':'PRIMITIVE','id':'f_imp_intro'})
        if 'f_exact' in known and g in ctx:out.append({'kind':'PRIMITIVE','id':'f_exact','hyp':ctx.index(g)})
        if 'f_imp_apply' in known:out += [{'kind':'PRIMITIVE','id':'f_imp_apply','hyp':i} for i,t in enumerate(ctx) if t.op=='Imp' and t.args[1]==g and t.args[0]!=g]
        if 'f_and_construct' in known and g.op=='And':out.append({'kind':'PRIMITIVE','id':'f_and_construct'})
        if 'f_and_cases' in known:out += [{'kind':'PRIMITIVE','id':'f_and_cases','hyp':i} for i,t in enumerate(ctx) if t.op=='And']
        candidates=sorted(self._ids(state,context),key=lambda s:s.identity);context.count('candidate_probes',2+3*len(ctx))
        for s in candidates:out.extend(self._calls(s,ctx,g,context))
        return [Action(a['id'],a) for a in out]
    def step(self,state,action,context):return p.step(state,action.payload)
    def potential(self,state,context):
        def cost(ctx,g):
            if g in ctx:return 0
            if any(not a['missing'] for s in self._ids(((ctx,g),),context) for a in self._calls(s,ctx,g,context)):return 1
            if g.op=='Imp':return 1+cost(ctx+(g.args[0],),g.args[1])
            if g.op=='And':return 1+cost(ctx,g.args[0])+cost(ctx,g.args[1])
            return 1 if any(t.op=='Imp' and t.args[1]==g for t in ctx) else 3
        return sum(cost(ctx,g) for ctx,g in state)
    def initial_priority(self,state,context):return (self.potential(state,context),0,0,0,0,0,0)
    def action_priority(self,state,child,action,depth,context,anchors):
        a=action.payload;ctx,g=state[0];s=context.library[action.structure_id];potential=self.potential(child,context)
        fit=next((r for r in anchors if r['id']==s.identity),{});binding=fit.get('alpha',{})
        agreement=sum(n in binding and p.d(t)==binding[n] for n,t in a.get('alpha',{}).items())
        unresolved=[p.fromdata(r['target']) for r in fit.get('mismatch_frontier',{}).get('unmatched_regions',())]
        call=bool(s.parents);gain=sum(ctx[i] in unresolved for i in a['known'].values()) if call else int(g in unresolved and (not child or len(child)<len(state)))
        return (potential,-(a.get('known_count',0) if call else int(s.identity=='f_exact')),-(self.potential(state,context)-potential),
                len(a.get('missing',())) if call else max(0,len(child)-len(state)+1),-s.level,depth,-agreement,-gain,-fit.get('score',0),len(context.library.primitives(s.identity)))
    def index_keys(self,s):
        if not s.parents or s.kernel.get('reference')=='lean.state_sequence':return ()
        key=s.metadata['schema_json']
        if key in self._key_cache:return self._key_cache[key]
        e=self.schema(s);pre,res=p.split(e);unresolved=set(p.slotnames(e))-set(p.slotnames(res));keys=[]
        for mask in range(8):
            ops={op for bit,op in enumerate(('Atom','Imp','And')) if mask&(1<<bit)}
            covered=set().union(*(set(p.slotnames(t)) for t in pre if t.op=='Slot' or t.op in ops))
            if unresolved<=covered:keys.append((res.op,mask))
        self._key_cache[key]=tuple(keys);return tuple(keys)
    def index_queries(self,state):
        ctx,g=state;mask=sum(1<<i for i,op in enumerate(('Atom','Imp','And')) if any(t.op==op for t in ctx))
        return ((g.op,mask),('Slot',mask))
    def abstract_instance(self,X,plan,context):
        schema,mapping=p.abstract_schema(X);templates=p.generalize_plan([a.payload for a in plan.actions],mapping)
        context.trace.append({'phase':'abstract','schema':p.d(schema),'templates':templates})
        return {'operation_id':'whole_F_proof','ports':PORTS,'alpha_schema':AlphaSchema(tuple(p.slotnames(schema))),
                'kernel':{'reference':'lean.certificate'},'metadata':{'schema_json':json.dumps(p.d(schema),sort_keys=True),
                'call_plan':[{'kind':a['kind'],'id':a['id']} for a in templates]}}
    def _helper(self,s,templates,view):
        schema=self.schema(s);names=p.slotnames(schema);pre,res=p.split(schema);alpha={n:Atom('slot_'+n) for n in names}
        lines,_=p.emit(view,templates,p.substitute(schema,alpha),alpha)
        signature=f'theorem lib_{s.identity} ('+' '.join('slot_'+n for n in names)+' : Prop) '
        signature+=' '.join(f'(premise_{i} : {p.render(t)})' for i,t in enumerate(pre))+f' : {p.render(res)} := by\n'
        return signature+'  exact (show '+p.render(schema)+' from by\n'+'\n'.join('    '+line for line in lines)+'\n  ) '+' '.join(f'premise_{i}' for i in range(len(pre)))
    def prepare_candidate(self,s,plan,context):
        templates=next(r['templates'] for r in reversed(context.trace) if r.get('phase')=='abstract');view=RuleView(self,context.library,(s,))
        metadata=thaw(s.metadata);metadata['proof_source']=self._helper(s,templates,view);s=replace(s,metadata=metadata)
        blocks=[]
        for j,X in enumerate((context.X,*context.validations)):
            X=self.validate_instance(X);binding=p.unify(self.schema(s),X)
            if binding is None:raise ValueError('validation is not an instance of abstract schema')
            lines,trace=p.emit(view,templates,X,binding);context.trace.append({'phase':'formation_execution','trace':trace})
            blocks.append(p.theorem('validation_'+str(j),X,lines))
        return s,Artifact(s.identity,self._helpers(context,(s.identity,),(s,))+'\n\n'.join(blocks),scope='formation')
    def artifact_from_plan(self,plan,context):
        schema,mapping=p.abstract_schema(context.X);templates=p.generalize_plan([a.payload for a in plan.actions],mapping)
        lines,trace=p.emit(RuleView(self,context.library),templates,context.X,p.unify(schema,context.X));context.trace.append({'phase':'execute_plan','trace':trace})
        return Artifact(source=self._helpers(context,tuple(a.structure_id for a in plan.actions))+p.theorem('completion',context.X,lines))
    def countermodel(self,X):
        names=sorted({n.args[0] for n in p.walk(X) if n.op=='Atom'})
        if len(names)>12:return None
        def truth(e,a):
            if e.op=='Atom':return a[e.args[0]]
            l,r=(truth(t,a) for t in e.args);return (not l or r) if e.op=='Imp' else l and r
        for values in itertools.product((False,True),repeat=len(names)):
            a=dict(zip(names,values))
            if not truth(X,a):return a
        return None
    def bootstrap(self,verifier=None):
        """Verify frozen foundational certificates, then use the common commit gate."""
        from ..runtime import ExecutionContext
        data=fixture();lib=Library();verifier=verifier or self.default_verifier();context=ExecutionContext(None,self,lib)
        record=VerificationRecord(True,'structural primitive rules',digest(sorted(VIEWS)))
        for sid in VIEWS:
            s=Leaf(sid,sid,PORTS,AlphaSchema(tuple(p.slotnames(VIEWS[sid]))),formation_provenance=FormationProvenance('frozen primitive rule',(),record.verifier,record.source_digest),kernel={'reference':'lean.primitive'})
            lib.commit(s,record)
        for sid,body in data['patterns'].items():
            old=next(s for s in data['raw']['structures'] if s['structure_id']==sid);parents=tuple(old['parent_ids']);e=p.fromdata(body)
            definition={'operation_id':'whole_F_proof','ports':PORTS,
                'alpha_schema':AlphaSchema(tuple(p.slotnames(e))),
                'kernel':{'reference':'lean.certificate'},
                'metadata':{'schema_json':json.dumps(body,sort_keys=True),
                    'call_plan':[{'kind':'PRIMITIVE','id':i} for i in parents]}}
            s=canonical_compose(parents,{'kind':'lean_proof_pattern','structure_key':sid,
                'schema_json':json.dumps(body,sort_keys=True)},definition,
                {item.identity:item.level for item in lib},source='frozen foundation')
            metadata=thaw(s.metadata);metadata['proof_source']=self._helper(s,thaw(s.metadata['call_plan']),RuleView(self,lib,(s,)));s=replace(s,metadata=metadata)
            verified=verifier.verify(Artifact(source=metadata['proof_source']),context)
            if not verified.passed:raise RuntimeError('foundation Lean verification failed: '+verified.stderr+verified.stdout)
            s=replace(s,formation_provenance=FormationProvenance('frozen verified foundation',parents,verified.verifier,verified.source_digest));lib.commit(s,verified)
        return lib
    def benchmark_projection_library(self,base,n,universal_certificate,builder):
        """Domain-side certificate instantiation, using Library.commit for every DAG node.

        This is benchmark foundation preparation, not engine.learn. The universal Lean
        proof is checked once; each member is a type substitution in that theorem.
        """
        if not universal_certificate.passed:raise ValueError('unverified projection rule')
        lib=Library()
        for s in base:lib.commit(s,VerificationRecord(True,s.formation_provenance.verifier,s.formation_provenance.verification_digest))
        count=len(self.retrieval_candidates(base));parents=('f_imp_intro','f_and_cases','f_exact')
        for i in range(n-count):
            sid=f'depth_distractor_{i:06}';e=builder(i);pre,res=p.split(e)
            if len(pre)!=1 or pre[0].op!='And' or pre[0].args[0]!=res:raise ValueError('not a universal projection substitution')
            names=p.slotnames(e);source=f'theorem lib_{sid} ('+' '.join('slot_'+name for name in names)+' : Prop) '
            source+='(premise_0 : '+p.render(pre[0])+') : '+p.render(res)+' := by\n  cases premise_0 with\n  | intro left right =>\n    exact left\n'
            record=VerificationRecord(True,'lean universal projection substitution',universal_certificate.source_digest)
            schema_json=json.dumps(p.d(e),sort_keys=True)
            definition={'operation_id':'whole_F_proof','ports':PORTS,
                'alpha_schema':AlphaSchema(tuple(names)),
                'kernel':{'reference':'lean.certificate'},
                'metadata':{'schema_json':schema_json,
                    'call_plan':[{'kind':'PRIMITIVE','id':pid} for pid in parents],
                    'proof_source':source}}
            s=canonical_compose(parents,{'kind':'lean_projection','structure_key':sid,
                'schema_json':schema_json},definition,
                {item.identity:item.level for item in lib},
                source='benchmark certified universal substitution')
            lib.commit(s,record)
        return lib
