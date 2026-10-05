// Generic JavaScript backend for recursively executing serialized F graphs.
function makeFExecutor(program) {
  const nodes = program.nodes;
  const primitive = {};
  for (const [id, s] of Object.entries(nodes)) {
    const role = (s.metadata || {}).primitive_role;
    if (role) primitive[role] = {id, s};
  }
  const mapping = primitive.operator_map.s.kernel.payload.mapping;
  const symbols = {};
  for (const [key, node] of Object.entries(mapping)) {
    if (node.kind === 'Binary' || node.kind === 'Unary') symbols[node.operator] = key;
  }
  Object.assign(symbols, {'==':'Eq','===':'Eq','!=':'NotEq','!==':'NotEq'});
  const dispatches = [], primitiveDispatches = [];
  let maxDepth = 0, callCount = 0, primitiveCallCount = 0;
  function primitiveCall(role, operation, depth) {
    primitiveCallCount++;
    const p = primitive[role];
    if (!p) throw new Error('required primitive f missing: ' + role);
    primitiveDispatches.push({structure_id:p.id, operation_id:p.s.operation_id, role, operation, depth});
    if (role === 'operator_map') {
      if (!symbols[operation]) throw new Error('operator primitive has no template for ' + operation);
      return symbols[operation];
    }
    return p.id;
  }
  function ev(e, env, depth) {
    if (e === null || typeof e !== 'object') return e;
    if (Object.prototype.hasOwnProperty.call(e,'lit')) { primitiveCall('literal_binding',e.lit,depth); return e.lit; }
    if (Object.prototype.hasOwnProperty.call(e,'var')) { primitiveCall('identifier_binding',e.var,depth); return env[e.var]; }
    if (Object.prototype.hasOwnProperty.call(e,'length')) { const x=ev(e.length,env,depth); primitiveCall('field_access','length',depth); return x.length; }
    if (Object.prototype.hasOwnProperty.call(e,'index')) { const x=ev(e.index,env,depth), i=ev(e.at,env,depth); primitiveCall('field_access','index',depth); return x[i]; }
    if (Object.prototype.hasOwnProperty.call(e,'select')) return ev(ev(e.select,env,depth)?e.yes:e.no,env,depth);
    if (Object.prototype.hasOwnProperty.call(e,'op')) {
      const a=ev(e.left,env,depth), b=ev(e.right,env,depth);
      const key=primitiveCall('operator_map',e.op,depth), op=mapping[key].operator;
      switch(op) {
        case '+': return a+b; case '-': return a-b; case '*': return a*b; case '%': return a%b;
        case '>': return a>b; case '>=': return a>=b; case '<': return a<b; case '<=': return a<=b;
        case '===': case '==': return a===b; case '!==': case '!=': return a!==b;
        default: throw new Error('unsupported generic operator '+op);
      }
    }
    throw new Error('invalid expression graph');
  }
  function call(id, bindings, env, depth=1, parent=null) {
    const s=nodes[id]; if (!s) throw new Error('recursive F child not found: '+id);
    const exe=s.kernel.executable; if (!exe) throw new Error('F has no executable opcode: '+id);
    maxDepth=Math.max(maxDepth,depth); callCount++;
    dispatches.push({structure_id:id,operation_id:s.operation_id,depth,parent,opcode:exe.op});
    switch(exe.op) {
      case 'sequence':
        for (const item of (bindings.steps||exe.steps||[])) {
          const bound={}; for (const [k,v] of Object.entries(item.bindings||{})) bound[k]=ev(v,env,depth);
          const out=call(item.f,bound,env,depth+1,id); if (out && out.__return) return out;
        }
        return null;
      case 'assign': env[bindings.target]=ev(bindings.value,env,depth); return null;
      case 'compare': {
        const a=ev(bindings.left,env,depth), b=ev(bindings.right,env,depth);
        const key=primitiveCall('operator_map',bindings.operator,depth), op=mapping[key].operator;
        const f={'>':()=>a>b,'>=':()=>a>=b,'<':()=>a<b,'<=':()=>a<=b,'===':()=>a===b,'!==':()=>a!==b,'==':()=>a===b,'!=':()=>a!==b}[op];
        if (!f) throw new Error('unsupported comparison '+op); env[bindings.out]=f(); return null;
      }
      case 'conditional_update': env[bindings.target]=ev(env[bindings.condition]?bindings.then:bindings.otherwise,env,depth); return null;
      case 'for_range': {
        const lo=ev(bindings.start,env,depth), hi=ev(bindings.stop,env,depth);
        for(let i=lo;i<hi;i++){env[bindings.variable]=i;call(bindings.body_f,{},env,depth+1,id);} return null;
      }
      case 'return': return {__return:true,value:ev(bindings.value,env,depth)};
      case 'binary_apply': {
        const leftEnv={...env}, rightEnv={...env};
        let left=call(exe.left_f,{},leftEnv,depth+1,id);
        let right=call(exe.right_f,{},rightEnv,depth+1,id);
        if (left&&left.__return) left=left.value;
        if (right&&right.__return) right=right.value;
        return ev({op:exe.operator,left,right},env,depth);
      }
      default: throw new Error('unknown executable F opcode '+exe.op);
    }
  }
  return {run(args,detailed=false) {dispatches.length=0;primitiveDispatches.length=0;maxDepth=0;callCount=0;primitiveCallCount=0;const env={};(program.input_slots||['nums']).forEach((k,i)=>env[k]=args[i]);const v=call(program.root_id,{},env);return {result:v&&v.__return?v.value:v,dispatches:detailed?dispatches.slice():undefined,primitive_dispatches:detailed?primitiveDispatches.slice():undefined,max_runtime_F_depth:maxDepth,recursive_F_dispatch_count:callCount,primitive_dispatch_count:primitiveCallCount};}};
}
