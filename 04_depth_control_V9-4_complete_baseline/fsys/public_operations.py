"""Public, origin-neutral operation contracts for Composer search."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


PROTOCOL_VERSION='fsys-public-operation/1'


@dataclass(frozen=True)
class PublicPort:
    name: str
    direction: str
    dtype: str
    cardinality: str

    def to_dict(self):
        return {'name':self.name,'direction':self.direction,'type':self.dtype,
                'cardinality':self.cardinality}


@dataclass(frozen=True)
class PublicOperation:
    protocol_version: str
    identity: str
    operation_id: str
    capability_descriptor: str
    kind: str
    ports: tuple[PublicPort,...]
    formal_slots: tuple[str,...]
    binding_schema: tuple[tuple[str,str,str],...]
    execution_entry: str
    context_slots: tuple[tuple[str,str],...]=()

    @property
    def input_ports(self):return tuple(p for p in self.ports if p.direction=='input')
    @property
    def output_ports(self):return tuple(p for p in self.ports if p.direction=='output')
    @property
    def type_constraints(self):
        return tuple((p.direction,p.dtype,p.cardinality) for p in self.ports)
    def to_dict(self):
        return {'protocol_version':self.protocol_version,'identity':self.identity,
            'operation_id':self.operation_id,'capability_descriptor':self.capability_descriptor,
            'kind':self.kind,'ports':[p.to_dict() for p in self.ports],
            'formal_slots':list(self.formal_slots),
            'binding_schema':[{'formal_slot':slot,'port':port,'type':dtype}
                              for slot,port,dtype in self.binding_schema],
            'type_constraints':[{'direction':direction,'type':dtype,'cardinality':cardinality}
                                for direction,dtype,cardinality in self.type_constraints],
            'execution_entry':self.execution_entry,
            'context_slots':[{'name':name,'type':dtype} for name,dtype in self.context_slots]}


def normalize_operation(structure,public_contract=None):
    """Build a public projection without reading parents, kernel, or provenance."""
    if isinstance(structure,Mapping):
        identity=str(structure.get('structure_id',structure.get('identity','')))
        operation_id=str(structure.get('operation_id',''))
        ports=tuple(PublicPort(str(p['name']),str(p['direction']),
                               str(p.get('type',p.get('dtype',''))),
                               str(p.get('cardinality','value'))) for p in structure.get('ports',()))
        alpha_schema=structure.get('alpha_schema',{})
        slots=tuple(alpha_schema.get('binding_slots',()))
        metadata=structure.get('metadata',{})
    else:
        identity=structure.identity
        operation_id=structure.operation_id
        ports=tuple(PublicPort(p.name,p.direction,p.dtype,p.cardinality) for p in structure.ports)
        slots=tuple(structure.alpha_schema.binding_slots)
        metadata=getattr(structure,'metadata',None)
    inputs=tuple(p for p in ports if p.direction=='input')
    outputs=tuple(p for p in ports if p.direction=='output')
    interface=None
    if metadata is not None:
        # Read exactly the one public contract key. Enumerating/copying the
        # complete metadata map can expose call plans and formation internals.
        interface=metadata.get('public_operation_interface')
    interface=public_contract or interface
    context_slots=()
    if interface:
        if interface.get('protocol_version',PROTOCOL_VERSION)!=PROTOCOL_VERSION:
            raise ValueError('unsupported public operation protocol')
        if tuple(interface.get('formal_slots',slots))!=slots:
            raise ValueError('public contract must preserve stored formal slots')
        if interface.get('ports'):
            ports=tuple(PublicPort(str(p['name']),str(p['direction']),str(p.get('type',p.get('dtype',''))),str(p.get('cardinality','value'))) for p in interface['ports'])
        context_slots=tuple((str(p['name']),str(p['type'])) for p in interface.get('context_slots',()))
        kind=str(interface.get('kind','operation'))
        capability=str(interface.get('capability',operation_id))
        entry=str(interface.get('execution_entry','Structure.execute'))
        schema=tuple((str(row['formal_slot']),str(row['port']),str(row['type']))
                     for row in interface.get('binding_schema',()))
    else:
        kind='operation' if inputs and outputs and len(slots)==len(inputs) else 'interface_deficient'
        capability=operation_id
        entry='Structure.execute'
        schema=tuple((slot,port.name,port.dtype) for slot,port in zip(slots,inputs))
    return PublicOperation(PROTOCOL_VERSION,identity,operation_id,
        capability,kind,ports,slots,schema,entry,context_slots)


def is_first_class_operation(value):
    interface=value if isinstance(value,PublicOperation) else normalize_operation(value)
    source_types={p.name:p.dtype for p in interface.input_ports}
    source_types.update({'$context.'+name:dtype for name,dtype in interface.context_slots})
    return (interface.protocol_version==PROTOCOL_VERSION
        and interface.kind=='operation'
        and bool(interface.identity and interface.operation_id and interface.execution_entry)
        and bool(interface.input_ports and interface.output_ports)
        and len(interface.binding_schema)==len(interface.formal_slots)
        and {row[0] for row in interface.binding_schema}==set(interface.formal_slots)
        and all(source_types.get(row[1])==row[2] for row in interface.binding_schema)
        and {p.name for p in interface.input_ports}.issubset({row[1] for row in interface.binding_schema}))


def compatible_operations(operations,goal):
    expected_inputs=tuple(str(row.get('type','')) for row in goal.inputs)
    expected_outputs=(str(goal.output.get('type','')),)
    compatible=[]
    for interface in operations:
        if not is_first_class_operation(interface):continue
        if tuple(port.dtype for port in interface.input_ports)!=expected_inputs:continue
        if tuple(port.dtype for port in interface.output_ports)!=expected_outputs:continue
        compatible.append(interface)
    return compatible


def bind_operation(interface,goal,runtime_values=None,runtime_context=None):
    if not is_first_class_operation(interface):raise ValueError('operation has no complete public binding contract')
    if len(goal.inputs)!=len(interface.input_ports):raise ValueError('goal input count does not match public ports')
    if runtime_values is not None and len(runtime_values)!=len(interface.input_ports):
        raise ValueError('runtime values do not match public formal slots')
    bound={}
    port_indexes={port.name:index for index,port in enumerate(interface.input_ports)}
    for slot,port_name,dtype in interface.binding_schema:
        if port_name.startswith('$context.'):
            name=port_name[len('$context.'):]
            if runtime_context is not None:
                if name not in runtime_context:raise ValueError('missing public runtime context slot: '+name)
                bound[slot]=runtime_context[name]
            else:bound[slot]={'runtime_context':name}
            continue
        index=port_indexes[port_name]
        if str(goal.inputs[index].get('type',''))!=dtype:raise ValueError('public slot type mismatch')
        bound[slot]=({'goal_input':goal.inputs[index]['name']} if runtime_values is None
                     else runtime_values[index])
    return bound
