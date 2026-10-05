import itertools,time
from . import ast as v80
from .ast import *
def data(e):return e.to_data()
def nodes(e):
    return 1 if e.op in ('Atom', 'Slot') else 1 + sum((nodes(a) for a in e.args))

def atoms(e):
    return {e.args[0]} if e.op == 'Atom' else set().union(*(atoms(a) for a in e.args))

def bind(pattern, target, counters, alpha=None):
    counters['unification_operations'] += 1
    counters['ast_nodes_visited'] += 1
    alpha = {} if alpha is None else alpha.copy()
    if pattern.op == 'Slot':
        k = pattern.args[0]
        if k in alpha:
            return alpha if alpha[k] == target else None
        alpha[k] = target
        return alpha
    if pattern.op != target.op:
        return None
    if pattern.op == 'Atom':
        return alpha if pattern == target else None
    for p, t in zip(pattern.args, target.args):
        alpha = bind(p, t, counters, alpha)
        if alpha is None:
            return None
    return alpha

def matching(pattern, target):
    started = time.perf_counter_ns()
    counts = {'alpha_binding_attempts': 1, 'ast_nodes_visited': 0, 'unification_operations': 0}
    exact = bind(pattern, target, counts)
    domains = {}
    op_total = 0
    slot_total = 0

    def totals(p):
        nonlocal op_total, slot_total
        if p.op == 'Slot':
            slot_total += 1
        else:
            op_total += 1
            for a in p.args:
                totals(a)
    totals(pattern)

    def aligned(p, t):
        counts['ast_nodes_visited'] += 1
        if p.op == 'Slot':
            d = domains.setdefault(p.args[0], [])
            if t not in d:
                d.append(t)
        elif p.op == t.op:
            for a, b in zip(p.args, t.args):
                aligned(a, b)
    aligned(pattern, target)

    def evaluate(alpha):

        def walk(p, t):
            counts['ast_nodes_visited'] += 1
            if p.op == 'Slot':
                return (nodes(t), 0, 1) if alpha.get(p.args[0]) == t else (0, 0, 0)
            if p.op != t.op:
                return (0, 0, 0)
            cov, op, slot = (1, 1, 0)
            for a, b in zip(p.args, t.args):
                c, o, s = walk(a, b)
                cov += c
                op += o
                slot += s
            return (cov, op, slot)
        c, o, s = walk(pattern, target)
        parts = {'top_level_compatibility': float(pattern.op == target.op), 'operator_compatibility': o / op_total, 'slot_consistency': s / slot_total, 'recursive_ast_coverage': c / nodes(target), 'matched_target_ast_nodes': c, 'target_ast_nodes': nodes(target)}
        score = 0.2 * parts['top_level_compatibility'] + 0.25 * parts['operator_compatibility'] + 0.25 * parts['slot_consistency'] + 0.3 * parts['recursive_ast_coverage']
        return (score, parts)
    if exact is not None:
        best = exact
        score, parts = evaluate(best)
        status = 'EXACT_BIND'
        assert abs(score - 1) < 1e-10
    else:
        best = {}
        score = -1
        parts = None
        keys = sorted(domains)
        for values in itertools.product(*(domains[k] for k in keys)):
            counts['alpha_binding_attempts'] += 1
            candidate = dict(zip(keys, values))
            sc, pa = evaluate(candidate)
            if sc > score:
                best, score, parts = (candidate, sc, pa)
        score = min(score, 0.99)
        status = 'PARTIAL_MATCH' if parts['matched_target_ast_nodes'] else 'NO_MATCH'
    return ({'binding_status': status, 'score': score, 'alpha': {k: data(v) for k, v in best.items()}, 'components': parts, **counts, 'wall_time_ns': time.perf_counter_ns() - started}, best)
