"""Generic recursive executable runtime for composite F call graphs."""
from __future__ import annotations
from collections.abc import Mapping
from .structure import Structure
from .serialization import thaw

class _DeferredKVNode:
    """Resolve only the KV node execution actually reaches."""
    def __init__(self,data):
        self.identity=data['structure_id'];self._data=data;self._loaded=None
    def __getattr__(self,name):
        if self._loaded is None:self._loaded=Structure.from_dict(self._data)
        return getattr(self._loaded,name)

class RecursiveFExecutor:
    def __init__(self,library,external_kv=None,*,trace=False):
        self.nodes={s.identity:s for s in library}
        if external_kv:
            records=external_kv.records.values() if hasattr(external_kv,'records') else external_kv
            for record in records:
                if record.get('status') in ('CANDIDATE','VALIDATED','PROMOTABLE','PERSISTENT'):
                    obj=_DeferredKVNode(record['structure']);self.nodes[obj.identity]=obj
        self.trace_enabled=trace;self.dispatches=[];self.primitive_dispatches=[];self.primitive_call_count=0;self.max_depth=0;self.active=[];self.call_count=0
        self.primitive={dict(s.metadata).get('primitive_role'):s for s in library if dict(s.metadata).get('translation_primitive')}
        operator_kernel=thaw(self.primitive['operator_map'].kernel)
        self.operator_templates=operator_kernel['payload']['mapping']
        self._symbols={}
        for name,temp in self.operator_templates.items():
            if temp.get('kind')=='Binary':self._symbols[temp['operator']]=name
            elif temp.get('kind')=='Unary':self._symbols[temp['operator']]=name
        self._symbols.update({'==':'Eq','===':'Eq','!=':'NotEq','!==':'NotEq'})
    def _primitive_call(self,role,operation=None,depth=0):
        self.primitive_call_count+=1
        obj=self.primitive.get(role)
        if obj is None:raise LookupError('required primitive f missing: '+role)
        if self.trace_enabled:self.primitive_dispatches.append({'structure_id':obj.identity,'operation_id':obj.operation_id,'role':role,'operation':operation,'depth':depth})
        if role=='operator_map':
            token=operation
            key=self._symbols.get(token)
            if key is None:raise LookupError('operator primitive has no template for '+str(token))
            return key
        return obj.identity
    def _eval(self,e,env,depth):
        if not isinstance(e,Mapping):return e
        if 'lit' in e:
            self._primitive_call('literal_binding',e['lit'],depth);return e['lit']
        if 'var' in e:
            self._primitive_call('identifier_binding',e['var'],depth);return env[e['var']]
        if 'length' in e:
            v=self._eval(e['length'],env,depth);self._primitive_call('field_access','length',depth);return len(v)
        if 'index' in e:
            v=self._eval(e['index'],env,depth);i=self._eval(e['at'],env,depth);self._primitive_call('field_access','index',depth);return v[i]
        if 'select' in e:
            cond=self._eval(e['select'],env,depth);return self._eval(e['yes'] if cond else e['no'],env,depth)
        if 'op' in e:
            left=self._eval(e['left'],env,depth);right=self._eval(e['right'],env,depth)
            key=self._primitive_call('operator_map',e['op'],depth)
            op=self.operator_templates[key]['operator']
            if op=='+':return left+right
            if op=='-':return left-right
            if op=='*':return left*right
            if op=='%':return left%right
            if op=='>':return left>right
            if op=='>=':return left>=right
            if op=='<':return left<right
            if op=='<=':return left<=right
            if op in ('===','=='):return left==right
            if op in ('!==','!='):return left!=right
            raise LookupError('generic operator runtime does not support '+op)
        raise ValueError('invalid expression graph '+repr(e))
    def _call(self,structure_id,bindings,env,depth=1,parent=None):
        if structure_id not in self.nodes:raise LookupError('recursive F child not found: '+structure_id)
        s=self.nodes[structure_id];kernel=thaw(s.kernel);exe=kernel.get('executable')
        if not exe:raise ValueError('F has no executable composition opcode: '+structure_id)
        self.max_depth=max(self.max_depth,depth);self.call_count+=1
        if structure_id in self.active:raise RuntimeError('recursive F cycle detected')
        if self.trace_enabled:self.dispatches.append({'structure_id':structure_id,'operation_id':s.operation_id,'depth':depth,'parent':parent,'opcode':exe['op'],'bindings':dict(bindings)})
        self.active.append(structure_id)
        try:
            op=exe['op']
            if op=='sequence':
                for item in bindings.get('steps',exe.get('steps',[])):
                    bound={k:self._eval(v,env,depth) for k,v in item.get('bindings',{}).items()}
                    value=self._call(item['f'],bound,env,depth+1,structure_id)
                    if isinstance(value,_Return):return value
                return None
            if op=='assign':env[bindings['target']]=self._eval(bindings['value'],env,depth);return None
            if op=='compare':
                left=self._eval(bindings['left'],env,depth);right=self._eval(bindings['right'],env,depth)
                key=self._primitive_call('operator_map',bindings['operator'],depth);symbol=self.operator_templates[key]['operator']
                env[bindings['out']]={'>' :lambda:left>right,'>=':lambda:left>=right,'<':lambda:left<right,'<=':lambda:left<=right,'==':lambda:left==right,'!=':lambda:left!=right,'===':lambda:left==right,'!==':lambda:left!=right}[symbol]()
                return None
            if op=='conditional_update':
                env[bindings['target']]=self._eval(bindings['then'] if env[bindings['condition']] else bindings['otherwise'],env,depth);return None
            if op=='for_range':
                lo=self._eval(bindings['start'],env,depth);hi=self._eval(bindings['stop'],env,depth)
                for i in range(lo,hi):
                    env[bindings['variable']]=i
                    self._call(bindings['body_f'],{},env,depth+1,structure_id)
                return None
            if op=='return':return _Return(self._eval(bindings['value'],env,depth))
            if op=='binary_apply':
                left_env=dict(env);right_env=dict(env)
                left=self._call(exe['left_f'],{},left_env,depth+1,structure_id)
                right=self._call(exe['right_f'],{},right_env,depth+1,structure_id)
                if isinstance(left,_Return):left=left.value
                if isinstance(right,_Return):right=right.value
                return self._eval({'op':exe['operator'],'left':left,'right':right},env,depth)
            raise ValueError('unknown executable F opcode '+str(op))
        finally:self.active.pop()
    def execute(self,root_id,args,*,input_slots=('nums',),bindings=None,runtime_state=None,_return_state=False):
        env=dict(runtime_state or {});env.update(zip(input_slots,args));value=self._call(root_id,bindings or {},env)
        result=value.value if isinstance(value,_Return) else value
        return (result,env) if _return_state else result

    def execute_public(self,interface,public_bindings):
        """Execute the selected edge using its declared external inputs/context."""
        if interface.execution_entry!='RecursiveFExecutor.execute':
            raise ValueError('unsupported recursive execution entry')
        if set(public_bindings)!=set(interface.formal_slots):
            raise ValueError('execution requires exactly the public formal slots')
        sources={port:slot for slot,port,_ in interface.binding_schema}
        args=[public_bindings[sources[port.name]] for port in interface.input_ports]
        state={}
        for slot,port,_ in interface.binding_schema:
            if port=='$context.STATE':state.update(public_bindings[slot])
            elif port.startswith('$context.'):state[port[len('$context.'):]]=public_bindings[slot]
        state_output=any(port.dtype=='runtime_state' for port in interface.output_ports)
        result=self.execute(interface.identity,args,input_slots=tuple(port.name for port in interface.input_ports),
                            runtime_state=state,_return_state=state_output)
        return result[1] if state_output else result

class _Return:
    def __init__(self,value):self.value=value

def program_snapshot(library,root_id,external_kv=None):
    resolver=RecursiveFExecutor(library,external_kv)
    if root_id not in resolver.nodes:raise KeyError(root_id)
    needed=set();
    def visit(sid):
        if sid in needed:return
        needed.add(sid);s=resolver.nodes[sid]
        for parent in s.parents:
            if parent in resolver.nodes:visit(parent)
        exe=thaw(s.kernel).get('executable',{})
        for item in exe.get('steps',[]):visit(item['f'])
        for child in exe.get('children',[]):visit(child)
    visit(root_id)
    return {'root_id':root_id,'input_slots':['nums'],'nodes':{sid:resolver.nodes[sid].to_dict() for sid in sorted(needed)}}

def execute_program_data(program,args,*,trace=False):
    # A small read-only adapter presents the embedded nodes as a Library-like iterable.
    class _Snapshot:
        def __iter__(self):return iter([Structure.from_dict(v) for v in program['nodes'].values()])
    executor=RecursiveFExecutor(_Snapshot(),trace=trace)
    value=executor.execute(program['root_id'],args,input_slots=program.get('input_slots',['nums']))
    return value,{'dispatches':executor.dispatches,'primitive_dispatches':executor.primitive_dispatches,
                  'max_runtime_F_depth':executor.max_depth,'recursive_F_dispatch_count':executor.call_count,
                  'primitive_dispatch_count':executor.primitive_call_count}

def render_python_harness(program):
    import json
    encoded=json.dumps(program,ensure_ascii=False,separators=(',',':'))
    return "import json\nfrom fsys.recursive_f_runtime import execute_program_data\n_PROGRAM = json.loads("+repr(encoded)+")\ndef synthesized(nums):\n    return execute_program_data(_PROGRAM, [nums])[0]\ndef synthesized_trace(nums):\n    return execute_program_data(_PROGRAM, [nums])\n"

def render_javascript_server(program):
    import json
    from pathlib import Path
    runtime=Path(__file__).with_suffix('.js').read_text(encoding='utf-8')
    encoded=json.dumps(program,ensure_ascii=False,separators=(',',':'))
    return runtime+"\nconst PROGRAM="+encoded+";const RUNNER=makeFExecutor(PROGRAM);const rl=require('readline').createInterface({input:process.stdin});rl.on('line',line=>{try{const r=JSON.parse(line);console.log(JSON.stringify(RUNNER.run(r.args||r,!!r.trace)))}catch(e){console.log(JSON.stringify({error:String(e)}))}});\n"
