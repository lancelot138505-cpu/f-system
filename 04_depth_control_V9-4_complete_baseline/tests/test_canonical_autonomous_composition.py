from .common import *

import json
from fsys.autonomous_composition import AutonomousCompositionDiscovery
from fsys.formation import canonical_compose
from fsys.serialization import FORBIDDEN, digest, thaw
from fsys.verifier import VerificationRecord


def operation_contract(structure):
    data = thaw(structure.to_dict())
    data.pop('formation_provenance')
    return data


def composition_definition(engine, children, topology, *, fixed_slots=None):
    first = engine.library[children[0]]
    slots = tuple(fixed_slots) if fixed_slots is not None else first.alpha_schema.binding_slots
    return {'operation_id': 'numeric.sequence', 'ports': first.ports,
        'alpha_schema': fs.AlphaSchema(slots),
        'kernel': {'reference': 'numeric.sequence', 'call_plan': list(children)},
        'metadata': {'call_plan': list(children), 'slot_policy': topology['slot_policy']}}


class TestCanonicalAutonomousComposition(unittest.TestCase):
    def test_same_composition_has_one_f_semantics_across_four_cases(self):
        engine = numeric_engine()
        discovery = AutonomousCompositionDiscovery()
        base = ('f_double', 'f_increment')

        child4_definition = composition_definition(engine, base,
            {'slot_policy': 'explicit_four_formals'},
            fixed_slots=('s000', 's001', 's002', 's003'))
        child4 = engine.formation.canonical_compose(base,
            {'kind': 'two_child_sequence', 'wiring': []}, child4_definition,
            source='ordinary composition')
        engine.library.commit(child4, VerificationRecord(True, 'test composition audit', digest(child4.kernel)))

        cases = [
            ('one-child', ('f_increment',), {'kind': 'unary_wrapper', 'slot_policy': 'inherit'}),
            ('two-child', base, {'kind': 'two_child_sequence', 'wiring': [], 'slot_policy': 'domain'}),
            ('higher-order', (child4.identity, 'f_increment'),
             {'kind': 'f_as_child_sequence', 'wiring': [], 'slot_policy': 'inherit'}),
            ('four-slot-fixed-binding', (child4.identity,),
             {'kind': 'unconnected_passthrough', 'slot_policy': 'inherit'}),
        ]
        results = []
        for label, children, topology in cases:
            definition = composition_definition(engine, children, topology,
                fixed_slots=('s000', 's001', 's002', 's003') if label == 'four-slot-fixed-binding' else None)
            ordinary = engine.formation.canonical_compose(children, topology, definition,
                source='ordinary composition')
            traces = [
                {'success': True, 'compositions': [{'children': children, 'topology': topology,
                    'observed_bindings': {'s000': 'trace-local-instance', 's001': 7,
                        's002': 7, 's003': 7}, 'observed_alpha': 'trace-local-alpha'}]},
                {'success': True, 'compositions': [{'children': children, 'topology': topology,
                    'observed_bindings': {'s000': 'trace-local-instance', 's001': 7,
                        's002': 7, 's003': 7}, 'observed_alpha': 'trace-local-alpha'}]},
            ]
            proposal, = discovery.discover(traces)
            autonomous_definition = composition_definition(engine, proposal['children'], proposal['topology'],
                fixed_slots=('s000', 's001', 's002', 's003') if label == 'four-slot-fixed-binding' else None)
            autonomous = engine.formation.canonical_compose(proposal['children'], proposal['topology'],
                autonomous_definition, source='autonomous discovery', evidence={'recurrence_count': 2})

            self.assertEqual(ordinary.identity, autonomous.identity, label)
            self.assertEqual(operation_contract(ordinary), operation_contract(autonomous), label)
            self.assertEqual(digest(operation_contract(ordinary)), digest(operation_contract(autonomous)), label)
            self.assertNotEqual(ordinary.formation_provenance.source,
                                autonomous.formation_provenance.source, label)
            self.assertEqual(ordinary.alpha_schema.binding_slots,
                             autonomous.alpha_schema.binding_slots, label)
            if label == 'four-slot-fixed-binding':
                self.assertEqual(ordinary.alpha_schema.binding_slots,
                                 ('s000', 's001', 's002', 's003'))
            ordinary_value = engine.domain.value(ordinary, 3.0, engine.library)
            autonomous_value = engine.domain.value(autonomous, 3.0, engine.library)
            self.assertEqual(ordinary_value, autonomous_value, label)
            results.append({'case': label, 'identity_equal': True,
                'contract_equal': True, 'slots': list(ordinary.alpha_schema.binding_slots),
                'output_equal': ordinary_value == autonomous_value,
                'children': list(children), 'topology': topology})

        persisted = json.dumps([operation_contract(r) for r in (ordinary, autonomous)], sort_keys=True)
        self.assertNotIn('trace-local-instance', persisted)
        self.assertNotIn('trace-local-alpha', persisted)
        self.assertFalse(FORBIDDEN.intersection(self._all_keys(operation_contract(autonomous))))
        self.assertEqual(len(results), 4)

    @staticmethod
    def _all_keys(value):
        if isinstance(value, dict):
            return set(value).union(*(TestCanonicalAutonomousComposition._all_keys(v) for v in value.values()))
        if isinstance(value, list):
            return set().union(*(TestCanonicalAutonomousComposition._all_keys(v) for v in value))
        return set()

    def test_trace_recurrence_does_not_infer_formal_slot_existence(self):
        topology = {'kind': 'fixed_child_call', 'wiring': []}
        traces = [{'success': True, 'compositions': [{'children': ['F_child4'],
            'topology': topology, 'observed_bindings': {'s000': 5, 's001': 5,
                's002': 5, 's003': 5}}]} for _ in range(3)]
        proposal, = AutonomousCompositionDiscovery().discover(traces)
        self.assertEqual(set(proposal), {'children', 'topology'})
        self.assertNotIn('observed_bindings', proposal)
