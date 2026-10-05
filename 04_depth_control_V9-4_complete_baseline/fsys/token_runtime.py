"""Cold-start token adapter and Library-backed translation VM.

This module knows generic Python AST record serialization and a small declarative
transformation VM. It contains no source-node translation table or JS emitter.
"""
from __future__ import annotations
import ast
from dataclasses import dataclass
from typing import Any
from .serialization import thaw

@dataclass
class SourceNode:
    type: str
    fields: dict[str, Any]

def source_record(value):
    """Serialize parser output generically; do not classify individual node types."""
    if isinstance(value, ast.AST):
        fields={}
        for name,child in ast.iter_fields(value):
            if name in {'ctx','type_ignores','type_comment','lineno','col_offset','end_lineno','end_col_offset'}: continue
            fields[name]=source_record(child)
        return SourceNode(type(value).__name__,fields)
    if isinstance(value,list): return [source_record(v) for v in value]
    if isinstance(value,tuple): return [source_record(v) for v in value]
    return value

class LibraryTranslationVM:
    """Executes declarative transformation instructions stored in core Library."""
    def __init__(self,library,usage=None):
        self.library=library;self.usage=usage
        self.rules={};self.primitives={};self.path=[];self.bindings=[];self.events=[];self._active_composites=[];self.metrics={'bootstrap_runtime_calls':0,'primitive_calls':0,'F_calls':0}
        for structure in library:
            kernel=dict(structure.kernel);meta=dict(structure.metadata)
            if meta.get('translation_primitive'):
                self.primitives[structure.operation_id]=structure.identity
            if meta.get('translation_rule'):
                self.rules[meta['source_type']]=structure.identity
        if not self.rules or not self.primitives: raise ValueError('translation F/primitives missing from Library')

    def _primitive(self,role):
        matches=[s for s in self.library if dict(s.metadata).get('primitive_role')==role]
        if not matches: raise LookupError('translation primitive missing: '+role)
        return matches[0]

    def _call_primitive(self,role,source,argument=None):
        s=self._primitive(role);self.path.append(s.identity);self.metrics['primitive_calls']+=1
        if self._active_composites and s.identity not in self._active_composites[-1].parents:
            raise ValueError(f'primitive {s.identity} is not a declared parent of active F {self._active_composites[-1].identity}')
        payload=thaw(s.kernel).get('payload',thaw(s.kernel))
        if role=='operator_map':
            key=source.type if isinstance(source,SourceNode) else str(source)
            mapping=payload['mapping']
            if key not in mapping: raise LookupError('operator translation missing: '+key)
            return mapping[key]
        if role=='operator_symbol':
            key=source.type if isinstance(source,SourceNode) else str(source)
            mapping=payload['symbols']
            if key not in mapping: raise LookupError('augmented operator translation missing: '+key)
            return mapping[key]
        if role=='field_access': return source.type if argument=='type' and isinstance(source,SourceNode) else source.fields[argument]
        if role=='target_constructor': return {'kind':argument,'fields':{}}
        if role=='block_builder': return {'kind':payload['target_kind'],payload['target_field']:argument.get('body',[])}
        if role=='identifier_binding': return {'kind':payload['target_kind'],payload['target_field']:source.fields[argument]}
        if role=='literal_binding': return {'kind':payload['target_kind'],payload['target_field']:source.fields[argument]}
        raise ValueError('unknown primitive role '+role)

    @staticmethod
    def _binding_value(value):
        if isinstance(value,SourceNode):return {'node':value.type}
        if isinstance(value,list):return [{'node':x.type} if isinstance(x,SourceNode) else x for x in value]
        return value
    def _field(self,node,name):
        value=self._call_primitive('field_access',node,name)
        self.bindings.append({'source_node':node.type,'slot':name,'value':self._binding_value(value)})
        return value
    def _operator(self,node): return self._call_primitive('operator_map',node)
    @staticmethod
    def _path_value(node,path):
        value=node
        for key in path:
            if isinstance(value,SourceNode): value=value.fields[key]
            elif isinstance(value,dict): value=value[key]
            else: raise ValueError('instruction path traverses a scalar')
        return value
    def _descriptor(self,descriptor,node):
        op=descriptor['op']
        if op=='literal': return {'kind':descriptor['kind'],'value':descriptor['value']}
        if op=='path_index':
            values=self._path_value(node,descriptor['path']);index=descriptor['index']
            value=values[index] if index<len(values) else descriptor.get('default')
            if value is None: raise ValueError('translation field index is missing')
            return self.translate(value) if isinstance(value,SourceNode) else value
        if op=='choose':
            for case in descriptor['cases']:
                condition=case['if'];values=self._path_value(node,condition['path'])
                if condition['op']=='length_equals' and len(values)==condition['value']:
                    return self._descriptor(case['value'],node)
            return self._descriptor(descriptor['default'],node)
        if op=='template':
            slots={name:self._descriptor(value,node) for name,value in descriptor['bindings'].items()}
            return self._instantiate(descriptor['template'],slots)
        if 'op' in descriptor: return self._expr(descriptor,node)
        return descriptor
    def _guard(self,guard,node):
        try:value=self._path_value(node,guard['path'])
        except (KeyError,TypeError):return False
        if guard['op']=='path_equals':return value==guard['value']
        if guard['op']=='path_not_equals':return value!=guard['value']
        if guard['op']=='length_between':return guard['minimum']<=len(value)<=guard['maximum']
        return False
    @staticmethod
    def _instantiate(template,slots):
        if isinstance(template,dict):
            if set(template)=={'$slot'}: return slots[template['$slot']]
            return {k:LibraryTranslationVM._instantiate(v,slots) for k,v in template.items()}
        if isinstance(template,list): return [LibraryTranslationVM._instantiate(v,slots) for v in template]
        return template

    def translate(self,source):
        if isinstance(source,list): return [self.translate(x) for x in source]
        if not isinstance(source,SourceNode): return source
        sid=self.rules.get(source.type)
        if sid is None: raise LookupError('translation F not found: '+source.type)
        structure=self.library[sid];meta=dict(structure.metadata);category=meta['category']
        # A category dispatcher F routes every child through the same Library index.
        dispatch_f=next((s for s in self.library if dict(s.metadata).get('dispatch_category')==category and source.type in dict(s.metadata).get('source_types',())),None)
        if dispatch_f is None: raise LookupError('translation composition dispatcher missing: '+category)
        dispatch_instruction=thaw(dispatch_f.kernel)['instruction']
        self._active_composites.append(dispatch_f)
        try:
            route_type=self._call_primitive('field_access',source,'type')
            if dispatch_instruction.get('op')!='route' or dispatch_instruction.get('category')!=category or route_type not in dict(dispatch_f.metadata).get('source_types',()):
                raise LookupError('translation composition route rejected: '+source.type)
        finally:self._active_composites.pop()
        self.path.append(dispatch_f.identity);self.metrics['F_calls']+=1
        self.path.append(structure.identity);self.metrics['F_calls']+=1
        event={'source_node':source.type,'matched_F':structure.identity,'operation_identity':structure.operation_id,
               'composition_dispatch_F':dispatch_f.identity,'target_kind':None}
        self.events.append(event)
        self._active_composites.append(structure)
        try:
            result=self._eval(thaw(structure.kernel)['instruction'],source)
            event['target_kind']=result.get('kind') if isinstance(result,dict) else type(result).__name__
            return result
        finally:self._active_composites.pop()

    def _expr(self,descriptor,node):
        op=descriptor['op']
        if op=='field': return self._field(node,descriptor['name'])
        if op=='translate': return self.translate(self._field(node,descriptor['name']))
        if op=='optional_translate':
            value=self._field(node,descriptor['name'])
            return None if value is None else self.translate(value)
        if op=='translate_list': return [self.translate(x) for x in self._field(node,descriptor['name'])]
        if op=='translate_first': return self.translate(self._field(node,descriptor['name'])[0])
        if op=='block':
            body=[self.translate(x) for x in self._field(node,descriptor['name'])]
            return self._call_primitive('block_builder',node,{'body':body})
        if op=='operator': return self._operator(self._field(node,descriptor['name']))
        if op=='operator_symbol': return self._call_primitive('operator_symbol',self._field(node,descriptor['name']))
        if op=='optional_block':
            values=self._field(node,descriptor['name'])
            if not values: return None
            body=[self.translate(x) for x in values]
            return self._call_primitive('block_builder',node,{'body':body})
        if op=='nested_translate_list':
            nested=self._field(node,descriptor['name'])
            return [self.translate(x) for x in nested.fields[descriptor['items']]]
        raise ValueError('unknown translation instruction: '+op)

    def _target(self,kind,fields,node):
        # The target constructor primitive performs the common IR allocation.
        target=self._call_primitive('target_constructor',node,kind)
        target['fields']=fields
        return target

    def _eval(self,instruction,node):
        op=instruction['op']
        if op=='identifier': return self._call_primitive('identifier_binding',node,instruction['field'])
        if op=='literal': return self._call_primitive('literal_binding',node,instruction['field'])
        if op=='target':
            if not all(self._guard(g,node) for g in instruction.get('guards',())):
                if 'fallback' in instruction:return self._eval(instruction['fallback'],node)
                raise ValueError('Library rule guard rejected source shape')
            fields={k:self._descriptor(v,node) if isinstance(v,dict) and 'op' in v else v for k,v in instruction['fields'].items()}
            return self._target(instruction['kind'],fields,node)
        if op=='binary':
            left=self._expr({'op':'translate','name':instruction['left']},node)
            right=self._expr({'op':'translate','name':instruction['right']},node)
            template=self._operator(self._field(node,instruction['operator']))
            return self._instantiate(template,{'left':left,'right':right})
        if op=='unary':
            operand=self._expr({'op':'translate','name':instruction['operand']},node)
            template=self._operator(self._field(node,instruction['operator']))
            return self._instantiate(template,{'operand':operand})
        if op=='fold_operator':
            values=self._expr({'op':'translate_list','name':instruction['values']},node)
            template=self._operator(self._field(node,instruction['operator']))
            out=values[0]
            for nxt in values[1:]: out=self._instantiate(template,{'left':out,'right':nxt})
            return out
        if op=='zip_fold':
            left=self._expr({'op':'translate','name':instruction['left']},node)
            comparators=self._field(node,instruction['items']);ops=self._field(node,instruction['operators'])
            pieces=[]
            for opnode,rightnode in zip(ops,comparators):
                right=self.translate(rightnode);pieces.append(self._instantiate(self._operator(opnode),{'left':left,'right':right}));left=right
            if len(pieces)==1:return pieces[0]
            out=pieces[0]
            for piece in pieces[1:]:out=self._instantiate(instruction['join_template'],{'left':out,'right':piece})
            return out
        raise ValueError('unknown Library instruction opcode: '+op)
