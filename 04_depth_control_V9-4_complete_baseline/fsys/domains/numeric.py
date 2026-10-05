"""A small numeric composition domain using the exact same framework lifecycle."""
from dataclasses import dataclass
import math,time
from ..domain import DomainAdapter,StructuralMatch,Action,Artifact
from ..structure import Leaf
from ..alpha import AlphaSchema,AlphaTensor
from ..ports import Port
from ..library import Library
from ..provenance import FormationProvenance
from ..verifier import RuleVerifier,VerificationRecord
from ..kernels import ZAKernel,CustomKernel
from ..serialization import digest

@dataclass(frozen=True)
class NumericProblem:
    input: float
    target: float
    examples: tuple = ()
    @property
    def pairs(self):return ((self.input,self.target),)+tuple(self.examples)

PORTS=(Port('value_in','input','float','value'),Port('value_out','output','float','value'))
class NumericDomain(DomainAdapter):
    name='tiny_numeric'
    def validate_instance(self,X):
        if not isinstance(X,NumericProblem) or not all(math.isfinite(float(v)) for pair in X.pairs for v in pair):raise ValueError('finite NumericProblem required')
        return X
    def state_type(self,X):return 'numeric_vector'
    def candidate_space(self,X):return tuple(dict.fromkeys(float(v) for pair in X.pairs for v in pair))
    def port_compatible(self,a,b):return a.direction=='output' and b.direction=='input' and a.dtype==b.dtype and a.cardinality==b.cardinality
    def structural_signature(self,s):return {'node_count':len(s.parents) or 1,'level':s.level,'root_operator':'numeric'}
    def value(self,s,x,library,context=None):
        if s.parents:
            for pid in s.parents:
                if context is not None:context.count('composition_expansions')
                x=self.value(library[pid],x,library,context)
            return x
        ref=s.kernel['reference']
        if ref=='za.linear':return float(x)*float(s.kernel['parameters']['A'][0][0])
        if ref=='numeric.increment':return float(x)+1.
        raise ValueError('unknown numeric kernel')
    def structural_exact(self,s,X,context=None):
        if context is None and s.parents:raise ValueError('composite matching requires runtime library context')
        lib=context.library if context else None
        exact=all(math.isclose(self.value(s,a,lib),b,abs_tol=1e-8) for a,b in X.pairs)
        return StructuralMatch(exact,{'input_value':float(X.input)} if exact else {},score=float(exact))
    def structural_partial(self,s,X,context=None,score=None):return {'id':s.identity,'score':0. if score is None else score,'alpha':{},'mismatch_frontier':{}}
    def encode_binding(self,s,X,binding,solver='direct_structural'):
        c=self.candidate_space(X);index=c.index(float(binding['input_value']))
        return AlphaTensor(tuple(4. if i==index else -4. for i in range(len(c))),(True,)*len(c),(1,len(c)),solver)
    def decode_alpha(self,s,X,alpha):
        c=self.candidate_space(X)
        if alpha.shape!=(1,len(c)):raise ValueError('dynamic K mismatch')
        active=[i for i in range(len(c)) if alpha.mask[i]]
        if not active:raise ValueError('empty numeric alpha')
        return {'input_value':c[max(active,key=lambda i:alpha.values[i])]}
    def binding_legal(self,s,X,binding):return binding.get('input_value')==float(X.input)
    def _execute(self,s,X,alpha,context):
        bound=self.decode_alpha(s,X,alpha)
        if not self.binding_legal(s,X,bound):raise ValueError('wrong runtime input routing')
        checks=tuple((self.value(s,a,context.library),b) for a,b in X.pairs)
        return Artifact(checks[0][0],checks=checks)
    execute_leaf=_execute
    execute_composite=_execute
    def execute_kernel(self,ref,params,X,alpha,context):
        x=float(X.input if isinstance(X,NumericProblem) else X)
        return x*float(params['A'][0][0]) if ref=='za.linear' else x+1.
    def verify_artifact(self,a):return bool(a.checks) and all(math.isclose(x,y,abs_tol=1e-8) for x,y in a.checks)
    def default_verifier(self):return RuleVerifier()
    def initial_state(self,X):return tuple(float(a) for a,b in X.pairs)
    def state_key(self,state):return tuple(round(x,8) for x in state)
    def is_complete(self,state,context):return all(math.isclose(x,b,abs_tol=1e-8) for x,(_,b) in zip(state,context.X.pairs))
    def initial_priority(self,state,context):return (0,)
    def search_actions(self,state,context):
        candidates=list(context.library)
        if context.search_scope is not None:candidates=[s for s in candidates if s.identity in context.search_scope]
        context.count('candidate_probes',len(candidates));context.count('structural_checks',len(candidates))
        return [Action(s.identity,()) for s in candidates]
    def step(self,state,action,context):return tuple(self.value(context.library[action.structure_id],v,context.library,context) for v in state)
    def action_priority(self,state,child,action,depth,context,anchors):return (depth,)
    def abstract_instance(self,X,plan,context):
        children=tuple(a.structure_id for a in plan.actions)
        return self.composition_definition_from_children(children,{'kind':'ordered_numeric_pipeline','order':'left_to_right'})
    def prepare_candidate(self,s,plan,context):
        checks=[]
        for X in (context.X,*context.validations):
            self.validate_instance(X);checks.extend((self.value(s,a,context.library),b) for a,b in X.pairs)
        return s,Artifact(checks[0][0],checks=tuple(checks),scope='formation')
    def artifact_from_plan(self,plan,context):
        values=self.initial_state(context.X)
        for a in plan.actions:values=self.step(values,a,context)
        return Artifact(values[0],checks=tuple((v,b) for v,(_,b) in zip(values,context.X.pairs)))
    def audit_structure(self,s):
        if s.alpha_schema.binding_slots!=('input_value',):return False
        if s.parents:return (s.operation_id=='numeric.sequence' or s.operation_id.startswith('numeric.sequence/')) and tuple(s.metadata.get('call_plan',()))==s.parents and s.kernel.get('reference')=='numeric.sequence'
        if s.operation_id=='numeric.increment':return s.kernel.get('reference')=='numeric.increment'
        if s.operation_id=='numeric.double':
            try:return s.kernel['reference']=='za.linear' and len(s.kernel['parameters']['A'])==1 and len(s.kernel['parameters']['A'][0])==1
            except (KeyError,TypeError):return False
        return False
    def composition_definition(self,left,right,wiring):
        children=(left.identity,right.identity)
        topology={'kind':'numeric_port_pipeline','wiring':tuple(tuple(edge) for edge in wiring)}
        return self.composition_definition_from_children(children,topology)
    def composition_definition_from_children(self,children,topology):
        children=tuple(children)
        operation_id='numeric.sequence/'+digest({'children':children,'topology':topology})[:20]
        return {'operation_id':operation_id,'ports':PORTS,'alpha_schema':AlphaSchema(('input_value',)),
                'topology':topology,'kernel':{'reference':'numeric.sequence'},
                'metadata':{'call_plan':list(children)}}
    def bootstrap(self):
        lib=Library();z=ZAKernel(1).fit([-1.,0.,1.],[-2.,0.,2.]);kernels=(('double',z),('increment',CustomKernel('numeric.increment')))
        for name,kernel in kernels:
            record=VerificationRecord(True,'independent foundation rules',digest(kernel.freeze()))
            s=Leaf('f_'+name,'numeric.'+name,PORTS,AlphaSchema(('input_value',)),formation_provenance=FormationProvenance('independent foundation',(),record.verifier,record.source_digest),kernel=kernel.freeze())
            if not all(math.isclose(self.value(s,x,lib),2*x if name=='double' else x+1,abs_tol=1e-8) for x in (-1,0,1)):raise ValueError('foundation failed')
            lib.commit(s,record)
        return lib
