"""Dumb JavaScript text renderer for target IR only; deliberately imports no Python AST."""
import json

def _expr(node):
    kind=node['kind'];f=node.get('fields',node)
    if kind=='Identifier': return str(node.get('name',f.get('name')))
    if kind=='Literal': return json.dumps(node.get('value',f.get('value')),ensure_ascii=False)
    if kind=='Array': return '['+', '.join(_expr(x) for x in f.get('elements',[]))+']'
    if kind=='Index': return _expr(f['object'])+'['+_expr(f['index'])+']'
    if kind=='Member': return _expr(f['object'])+'.'+str(f['property'])
    if kind=='Binary': return f"({_expr(f['left'])} {f['operator']} {_expr(f['right'])})"
    if kind=='Unary': return f"({f['operator']}{_expr(f['operand'])})"
    if kind=='Call': return f"{_expr(f['callee'])}({', '.join(_expr(a) for a in f['arguments'])})"
    if kind=='Conditional': return f"({_expr(f['test'])} ? {_expr(f['consequent'])} : {_expr(f['alternate'])})"
    if kind=='Update': return f"{_expr(f['target'])} {f['operator']} {_expr(f['value'])}"
    raise ValueError('not a JavaScript expression IR: '+str(kind))

def _block(node,level=0):
    body=node.get('body',node.get('fields',{}).get('body',[]))
    if isinstance(body,dict) and body.get('kind')=='Block':body=body.get('body',[])
    lines=[]
    for child in body:lines.extend(_stmt(child,level))
    return lines

def _stmt(node,level=0):
    kind=node['kind'];f=node.get('fields',node);p='  '*level
    if kind=='Assignment':return [p+_expr(f['target'])+' = '+_expr(f['value'])+';']
    if kind=='AugAssignment':return [p+_expr(f['target'])+' '+f['operator']+'= '+_expr(f['value'])+';']
    if kind=='ExpressionStatement':return [p+_expr(f['expression'])+';']
    if kind=='Return':return [p+'return'+(' '+_expr(f['value']) if f.get('value') is not None else '')+';']
    if kind=='If':
        out=[p+'if ('+_expr(f['condition'])+') {']+_block(f['body'],level+1)+[p+'}']
        other=f.get('else_body')
        if other:
            if other['kind']=='If':out[-1]+=' else '+_stmt(other,level)[0].lstrip();out+=_stmt(other,level)[1:]
            else:out[-1]+=' else {';out+=_block(other,level+1)+[p+'}']
        return out
    if kind=='While':return [p+'while ('+_expr(f['condition'])+') {']+_block(f['body'],level+1)+[p+'}']
    if kind=='For':
        v=_expr(f['variable']);start=_expr(f['start']);test=_expr(f['condition']);update=_expr(f['update'])
        return [p+f'for (let {v} = {start}; {test}; {update}) {{']+_block(f['body'],level+1)+[p+'}']
    if kind=='ForEach':
        return [p+'for (const '+_expr(f['variable'])+' of '+_expr(f['iterable'])+') {']+_block(f['body'],level+1)+[p+'}']
    if kind=='Break':return [p+'break;']
    if kind=='Continue':return [p+'continue;']
    if kind=='Function':
        args=', '.join(_expr(a) for a in f['arguments'])
        return [p+'function '+f['name']+'('+args+') {']+_block(f['body'],level+1)+[p+'}']
    if kind=='Block':return _block(node,level)
    raise ValueError('not a JavaScript statement IR: '+str(kind))

def render_target(target):
    """Render target IR. It never inspects Python syntax or source AST nodes."""
    if target['kind']!='Program': raise ValueError('top-level target must be Program')
    return '\n'.join(_block(target,0))+'\n'
