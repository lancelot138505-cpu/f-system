"""Public adapter for V9 token-library recursive algorithm operations.

The existing composed_algorithm declaration exposes the legacy sequence<int> ->
int callable boundary. Four stored category slots are preserved; the public
contract distinguishes the external sequence from runtime state channels. This
adapter reads operation declarations and formal slots, never formation internals.
"""
def recursive_operation_contract(formal_slots):
    slots=tuple(formal_slots)
    if slots!=('SEQUENCE','STATE','INDEX','BOOLEAN'):
        raise ValueError('unexpected stored token formal schema')
    return {'protocol_version':'fsys-public-operation/1','kind':'operation',
        'capability':'sequence<int> -> int','formal_slots':list(slots),
        'ports':[{'name':'nums','direction':'input','type':'sequence<int>'},
                 {'name':'result','direction':'output','type':'int'}],
        'context_slots':[{'name':'STATE','type':'runtime_state'},
                         {'name':'INDEX','type':'optional<int>'},
                         {'name':'BOOLEAN','type':'optional<bool>'}],
        'binding_schema':[{'formal_slot':'SEQUENCE','port':'nums','type':'sequence<int>'},
            {'formal_slot':'STATE','port':'$context.STATE','type':'runtime_state'},
            {'formal_slot':'INDEX','port':'$context.INDEX','type':'optional<int>'},
            {'formal_slot':'BOOLEAN','port':'$context.BOOLEAN','type':'optional<bool>'}],
        'execution_entry':'RecursiveFExecutor.execute'}


def recursive_step_operation_contract(formal_slots):
    """Public state-transition boundary for the internal adjacent-step F."""
    slots=tuple(formal_slots)
    if slots!=('SEQUENCE','STATE','INDEX','BOOLEAN'):
        raise ValueError('unexpected stored token formal schema')
    return {'protocol_version':'fsys-public-operation/1','kind':'operation',
        'capability':'sequence<int> -> runtime_state','formal_slots':list(slots),
        'ports':[{'name':'nums','direction':'input','type':'sequence<int>'},
                 {'name':'state_out','direction':'output','type':'runtime_state'}],
        'context_slots':[{'name':'STATE','type':'runtime_state'},
                         {'name':'INDEX','type':'optional<int>'},
                         {'name':'BOOLEAN','type':'optional<bool>'}],
        'binding_schema':[{'formal_slot':'SEQUENCE','port':'nums','type':'sequence<int>'},
            {'formal_slot':'STATE','port':'$context.STATE','type':'runtime_state'},
            {'formal_slot':'INDEX','port':'$context.INDEX','type':'optional<int>'},
            {'formal_slot':'BOOLEAN','port':'$context.BOOLEAN','type':'optional<bool>'}],
        'execution_entry':'RecursiveFExecutor.execute'}


def publish_recursive_operation_contracts(library):
    return {obj.identity:recursive_operation_contract(obj.alpha_schema.binding_slots)
            for obj in library if obj.metadata.get('exec_capability')=='composed_algorithm'}
