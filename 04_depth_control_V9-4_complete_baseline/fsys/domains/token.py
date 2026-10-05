"""Discrete token-domain experiment: structural AST templates persisted as fsys Structures.

The parser is only used to expose syntax and token roles.  The initial Python-to-JS
rendering table is an explicit BOOTSTRAP RULE set; see the experiment report.
"""
from __future__ import annotations
import ast, hashlib, json, re, tokenize, io
from dataclasses import dataclass
from typing import Any

from ..structure import Leaf
from ..alpha import AlphaSchema
from ..provenance import FormationProvenance
from ..serialization import digest
from ..verifier import VerificationRecord

@dataclass
class TokenInstance:
    value: str
    token_class: str
    role: str
    slot: int

@dataclass
class Program:
    source: str
    tree: ast.Module
    tokens: tuple[TokenInstance, ...]

def _token_class(t):
    if t.type == tokenize.NAME:
        if t.string in {'True','False'}: return 'BOOLEAN'
        if t.string in {'if','else','elif','while','for','in','range','def','return','and','or','not'}: return 'KEYWORD'
        return 'IDENTIFIER'
    if t.type == tokenize.NUMBER: return 'NUMBER_LITERAL'
    if t.type == tokenize.STRING: return 'STRING_LITERAL'
    if t.type == tokenize.OP: return 'OPERATOR'
    return None

def _roles(tree):
    roles={}
    def walk(n, role='SYNTAX'):
        if isinstance(n, ast.Name): roles.setdefault(n.id,[]).append(role)
        if isinstance(n, ast.Constant): roles.setdefault(repr(n.value),[]).append('LITERAL')
        if isinstance(n, ast.FunctionDef): roles.setdefault(n.name,[]).append('FUNCTION_NAME')
        if isinstance(n, ast.arg): roles.setdefault(n.arg,[]).append('ARGUMENT')
        for field, value in ast.iter_fields(n):
            if isinstance(value, ast.AST):
                childrole={'target':'ASSIGN_TARGET','targets':'ASSIGN_TARGET','left':'LEFT_OPERAND','right':'RIGHT_OPERAND',
                           'func':'FUNCTION_CALLEE','args':'ARGUMENT'}.get(field, field.upper())
                walk(value, childrole)
            elif isinstance(value,list):
                for item in value:
                    if isinstance(item,ast.AST): walk(item,field.upper())
    walk(tree)
    return roles

def parse_program(source: str) -> Program:
    tree=ast.parse(source)
    rolemap=_roles(tree); out=[]
    for i,t in enumerate(tokenize.generate_tokens(io.StringIO(source).readline)):
        cls=_token_class(t)
        if cls:
            val=t.string
            choices=rolemap.get(val,[])
            priority=('ASSIGN_TARGET','FUNCTION_NAME','ARGUMENT','FUNCTION_CALLEE','LEFT_OPERAND','RIGHT_OPERAND','LITERAL')
            role=next((r for r in priority if r in choices),choices[0] if choices else ('IDENTIFIER' if cls=='IDENTIFIER' else cls))
            out.append(TokenInstance(val,cls,role,i))
    return Program(source,tree,tuple(out))

def _shape(n: Any):
    """Value-free syntax signature; identifiers and literals become alpha slots."""
    if isinstance(n, ast.AST):
        attrs=[]
        for f,v in ast.iter_fields(n):
            if f in ('ctx',): continue
            if isinstance(n,ast.Name) and f=='id': v='$IDENTIFIER'
            elif isinstance(n,ast.arg) and f=='arg': v='$IDENTIFIER'
            elif isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and f=='name': v='$IDENTIFIER'
            elif isinstance(n,ast.Constant) and f=='value': v='$'+('BOOLEAN' if isinstance(v,bool) else 'NUMBER_LITERAL' if isinstance(v,(int,float)) else 'STRING_LITERAL' if isinstance(v,str) else 'CONSTANT')
            elif isinstance(v,list): v=[_shape(x) for x in v]
            elif isinstance(v,ast.AST): v=_shape(v)
            elif isinstance(v,(str,int,float,bool,type(None))): v=v
            else: v=type(v).__name__
            attrs.append([f,v])
        return [type(n).__name__,attrs]
    if isinstance(n,list): return [_shape(x) for x in n]
    return n

def signature(node): return json.dumps(_shape(node),sort_keys=True,separators=(',',':'))

class TokenLibrary:
    """Maps value-free tree signatures to committed core Structures."""
    def __init__(self, engine): self.engine=engine; self.by_signature={}
    def intern(self,node,counts):
        sig=signature(node)
        if sig in self.by_signature: return self.by_signature[sig],False
        child_nodes=[]
        for _,v in ast.iter_fields(node):
            if isinstance(v,ast.AST): child_nodes.append(v)
            elif isinstance(v,list): child_nodes.extend(x for x in v if isinstance(x,ast.AST))
        parent_ids=[]
        for child in child_nodes:
            sid,_=self.intern(child,counts); parent_ids.append(sid)
        structure_id=('f_' if not parent_ids else 'F_')+hashlib.sha256(sig.encode()).hexdigest()[:20]
        operation_id='token.op.'+hashlib.sha256(sig.encode()).hexdigest()[:16]
        kernel={'reference':'token.ast_template','signature':sig,'node_type':type(node).__name__,
                'child_signatures':[signature(c) for c in child_nodes],
                'token_slots':_slot_schema(node)}
        # Structural templates contain no source text, names, or concrete literal values.
        if parent_ids:
            definition={'operation_id':operation_id,'ports':(),
                'alpha_schema':AlphaSchema(tuple(kernel['token_slots'])),
                'kernel':kernel,'metadata':{'token_template':True}}
            obj=self.engine.formation.canonical_compose(parent_ids,
                {'kind':'token_ast_children','node_type':type(node).__name__,
                 'child_signatures':[signature(c) for c in child_nodes]},
                definition,source='token structural composition',
                evidence={'template_signature':sig})
            structure_id=obj.identity
        else:
            obj=Leaf(structure_id,operation_id,(),AlphaSchema(tuple(kernel['token_slots'])),formation_provenance=
                     FormationProvenance('token primitive abstraction',(),'token-template-verifier',digest(sig)),kernel=kernel,
                     metadata={'formation_path':'token class primitive'})
        receipt=VerificationRecord(True,'token structural template audit',digest({'signature':sig,'parents':parent_ids}))
        self.engine.library.commit(obj,receipt); self.by_signature[sig]=structure_id
        counts['new_F' if parent_ids else 'new_primitive']+=1
        return structure_id,True

def _slot_schema(node):
    slots=[]
    for x in ast.walk(node):
        if isinstance(x,(ast.Name,ast.arg)): slots.append('IDENTIFIER')
        elif isinstance(x,ast.Constant): slots.append('LITERAL')
    return list(dict.fromkeys(slots))

class PythonToJS:
    """BOOTSTRAP RULE: deterministic, value-preserving subset emitter."""
    RULES=('Name/argument/function-name -> identifier slot','Constant -> literal slot',
      'BinOp/UnaryOp/Compare/BoolOp -> JavaScript operator expression',
      'Assign/AugAssign/Expr/Return -> statement forms','If/While/For(range) -> braces and loop forms',
      'FunctionDef/Call -> function and call forms','suite indentation -> braces')
    def expr(self,n):
        if isinstance(n,ast.Name): return n.id
        if isinstance(n,ast.Constant): return json.dumps(n.value,ensure_ascii=False)
        if isinstance(n,ast.BinOp):
            a,b=self.expr(n.left),self.expr(n.right)
            ops={ast.Add:'+ ',ast.Sub:'- ',ast.Mult:'* ',ast.Div:'/ ',ast.Mod:'% ',ast.Pow:'** ',ast.FloorDiv:'// '}
            if isinstance(n.op,ast.FloorDiv): return f'Math.floor(({a}) / ({b}))'
            if isinstance(n.op,ast.Mod): return f'((({a}) % ({b}) + ({b})) % ({b}))'
            return f'({a} {ops[type(n.op)].strip()} {b})'
        if isinstance(n,ast.UnaryOp):
            op={ast.USub:'-',ast.UAdd:'+',ast.Not:'!'}.get(type(n.op))
            if not op: raise ValueError('unsupported unary operator')
            return f'({op}{self.expr(n.operand)})'
        if isinstance(n,ast.BoolOp): return '('+(' && ' if isinstance(n.op,ast.And) else ' || ').join(self.expr(v) for v in n.values)+')'
        if isinstance(n,ast.Compare):
            chunks=[];left=n.left
            for op,right in zip(n.ops,n.comparators):
                sym={ast.Eq:'===',ast.NotEq:'!==',ast.Gt:'>',ast.Lt:'<',ast.GtE:'>=',ast.LtE:'<='}.get(type(op))
                if not sym: raise ValueError('unsupported comparison')
                chunks.append(f'({self.expr(left)} {sym} {self.expr(right)})');left=right
            return '('+' && '.join(chunks)+')'
        if isinstance(n,ast.Call):
            if isinstance(n.func,ast.Name) and n.func.id=='range': raise ValueError('range only supported in for')
            return self.expr(n.func)+'('+', '.join(self.expr(a) for a in n.args)+')'
        raise ValueError('unsupported expression '+type(n).__name__)
    def block(self,body,level):
        lines=[]
        for n in body: lines.extend(self.stmt(n,level))
        return lines
    def stmt(self,n,l=0):
        p='  '*l
        if isinstance(n,ast.Assign):
            if len(n.targets)!=1: raise ValueError('multiple assignment unsupported')
            return [p+self.expr(n.targets[0])+' = '+self.expr(n.value)+';']
        if isinstance(n,ast.AugAssign):
            op={ast.Add:'+',ast.Sub:'-',ast.Mult:'*',ast.Div:'/'}.get(type(n.op))
            if not op: raise ValueError('unsupported augmented assignment')
            return [p+self.expr(n.target)+' '+op+'= '+self.expr(n.value)+';']
        if isinstance(n,ast.Expr): return [p+self.expr(n.value)+';']
        if isinstance(n,ast.Return): return [p+'return'+(' '+self.expr(n.value) if n.value else '')+';']
        if isinstance(n,ast.If):
            out=[p+'if ('+self.expr(n.test)+') {']+self.block(n.body,l+1)+[p+'}']
            if n.orelse:
                if len(n.orelse)==1 and isinstance(n.orelse[0],ast.If):
                    branch=self.stmt(n.orelse[0],l)
                    out[-1]+=' else '+branch[0].lstrip();out+=branch[1:]
                else: out[-1]+=' else {';out+=self.block(n.orelse,l+1)+[p+'}']
            return out
        if isinstance(n,ast.While): return [p+'while ('+self.expr(n.test)+') {']+self.block(n.body,l+1)+[p+'}']
        if isinstance(n,ast.For):
            if not isinstance(n.iter,ast.Call) or not isinstance(n.iter.func,ast.Name) or n.iter.func.id!='range' or not isinstance(n.target,ast.Name): raise ValueError('for supports range(...) only')
            args=n.iter.args
            if not 1<=len(args)<=3: raise ValueError('range arity')
            start='0' if len(args)==1 else self.expr(args[0]);stop=self.expr(args[0] if len(args)==1 else args[1]);step='1' if len(args)<3 else self.expr(args[2]);v=n.target.id
            # Python range has exclusive stop and supports negative step.
            test=f'(({step}) > 0 ? {v} < ({stop}) : {v} > ({stop}))'
            return [p+f'for (let {v} = {start}; {test}; {v} += ({step})) {{']+self.block(n.body,l+1)+[p+'}']
        if isinstance(n,ast.FunctionDef):
            return [p+'function '+n.name+'('+', '.join(a.arg for a in n.args.args)+') {']+self.block(n.body,l+1)+[p+'}']
        raise ValueError('unsupported statement '+type(n).__name__)
    def emit(self,program): return '\n'.join(self.block(program.tree.body,0))+'\n'

def walk_nodes(node):
    yield node
    for _,v in ast.iter_fields(node):
        if isinstance(v,ast.AST): yield from walk_nodes(v)
        elif isinstance(v,list):
            for x in v:
                if isinstance(x,ast.AST): yield from walk_nodes(x)
