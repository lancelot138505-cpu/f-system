import json,itertools
from . import matching as backend
from .ast import *
def d(e):
    return e.to_data()

def render(e):
    if e.op == 'Slot':
        return 'slot_' + e.args[0]
    if e.op == 'Atom':
        return e.args[0]
    return '(' + render(e.args[0]) + (' → ' if e.op == 'Imp' else ' ∧ ') + render(e.args[1]) + ')'

def walk(e):
    yield e
    if e.op in ('Imp', 'And'):
        for a in e.args:
            yield from walk(a)

def substitute(e, alpha):
    if e.op == 'Slot':
        return alpha[e.args[0]]
    if e.op == 'Atom':
        return e
    return P(e.op, tuple((substitute(a, alpha) for a in e.args)))

def slotnames(e):
    return sorted({n.args[0] for n in walk(e) if n.op == 'Slot'})

def split(e):
    premises = []
    while e.op == 'Imp':
        premises.append(e.args[0])
        e = e.args[1]
    return (premises, e)

def unify(p, t, alpha=None):
    return backend.v80.unify(p, t, alpha)

def frontier(pattern, target, alpha):
    matched = []
    unmatched = []
    operators = []
    slots = []

    def rec(p, t, path):
        if p.op == 'Slot':
            if alpha.get(p.args[0]) == t:
                matched.append({'path': path, 'target': d(t), 'slot': p.args[0]})
            else:
                slots.append({'path': path, 'slot': p.args[0], 'expected': d(alpha[p.args[0]]) if p.args[0] in alpha else None, 'actual': d(t)})
                unmatched.append({'path': path, 'target': d(t), 'reason': 'slot consistency mismatch'})
        elif p.op != t.op:
            operators.append({'path': path, 'schema_operator': p.op, 'target_operator': t.op})
            unmatched.append({'path': path, 'target': d(t), 'reason': 'operator mismatch'})
        else:
            matched.append({'path': path, 'operator': t.op})
            for i, (a, b) in enumerate(zip(p.args, t.args)):
                rec(a, b, path + [i])
    rec(pattern, target, [])
    targetpre, targetgoal = split(target)

    def covered(path):
        return not any((u['path'][:len(path)] == path or path[:len(u['path'])] == u['path'] for u in unmatched))
    paths = [[1] * i + [0] for i in range(len(targetpre))]
    return {'matched_regions': matched, 'unmatched_regions': unmatched, 'operator_mismatches': operators, 'unresolved_slots': slots, 'unmatched_theorem_premises': [{'index': i, 'path': p, 'type': d(e)} for i, (p, e) in enumerate(zip(paths, targetpre)) if not covered(p)], 'unmatched_goal_structure': None if covered([1] * len(targetpre)) else d(targetgoal)}

def concrete_key(goals):
    return tuple(((tuple(ctx), g) for ctx, g in goals))

def abstract_schema(target):
    names = sorted({e.args[0] for e in walk(target) if e.op == 'Atom'})
    alpha = {n: Slot('s' + str(i)) for i, n in enumerate(names)}

    def rec(e):
        return alpha[e.args[0]] if e.op == 'Atom' else P(e.op, tuple((rec(a) for a in e.args)))
    return (rec(target), alpha)

def abstract_expr(e, alpha):
    if e.op == 'Atom':
        return alpha[e.args[0]]
    return P(e.op, tuple((abstract_expr(a, alpha) for a in e.args)))

def fromdata(v):
    return P(v['op'], tuple((fromdata(a) if isinstance(a, dict) else a for a in v['args'])))

def call_actions(lib, sid, ctx, goal, unifier=None):
    unify = unifier or globals()['unify']
    schema = lib.schemas[sid]
    premises, result = split(schema)
    initial = unify(result, goal)
    if initial is None:
        return []
    possibilities = [initial]
    for premise in premises:
        added = []
        for alpha in possibilities:
            for hyp in ctx:
                bound = unify(premise, hyp, alpha)
                if bound is not None:
                    added.append(bound)
        possibilities += added
        unique = {digest({k: d(v) for k, v in a.items()}): a for a in possibilities}
        possibilities = list(unique.values())
    result_actions = []
    for a in possibilities:
        if set(a) != set(slotnames(schema)):
            continue
        concrete = [substitute(p, a) for p in premises]
        known = {i: ctx.index(p) for i, p in enumerate(concrete) if p in ctx}
        missing = [p for i, p in enumerate(concrete) if i not in known]
        if goal in missing:
            continue
        if not known and result.op == 'Slot':
            continue
        if not missing and goal in ctx:
            continue
        result_actions.append({'kind': 'CALL_F', 'id': sid, 'alpha': a, 'known': known, 'missing': missing, 'known_count': len(known)})
    return result_actions

def actions(lib, state, pool):
    ctx, g = state[0]
    out = []
    if 'f_imp_intro' in pool and g.op == 'Imp':
        out.append({'kind': 'PRIMITIVE', 'id': 'f_imp_intro'})
    if 'f_exact' in pool and g in ctx:
        out.append({'kind': 'PRIMITIVE', 'id': 'f_exact', 'hyp': ctx.index(g)})
    if 'f_imp_apply' in pool:
        out += [{'kind': 'PRIMITIVE', 'id': 'f_imp_apply', 'hyp': i} for i, t in enumerate(ctx) if t.op == 'Imp' and t.args[1] == g and (t.args[0] != g)]
    if 'f_and_construct' in pool and g.op == 'And':
        out.append({'kind': 'PRIMITIVE', 'id': 'f_and_construct'})
    if 'f_and_cases' in pool:
        out += [{'kind': 'PRIMITIVE', 'id': 'f_and_cases', 'hyp': i} for i, t in enumerate(ctx) if t.op == 'And']
    for sid in pool:
        if sid in lib.schemas:
            out += call_actions(lib, sid, ctx, g)
    return out

def step(state, action):
    ctx, g = state[0]
    rest = state[1:]
    sid = action['id']
    if action['kind'] == 'CALL_F':
        return tuple(((ctx, t) for t in action['missing'])) + rest
    if sid == 'f_imp_intro':
        return ((ctx + (g.args[0],), g.args[1]),) + rest
    if sid == 'f_exact':
        return rest
    if sid == 'f_imp_apply':
        return ((ctx, ctx[action['hyp']].args[0]),) + rest
    if sid == 'f_and_construct':
        return ((ctx, g.args[0]), (ctx, g.args[1])) + rest
    if sid == 'f_and_cases':
        i = action['hyp']
        t = ctx[i]
        return ((ctx[:i] + ctx[i + 1:] + t.args, g),) + rest
    raise ValueError('unknown primitive')

def remaining(state, lib=None):

    def cost(ctx, g):
        if g in ctx:
            return 0
        if lib is not None and any((not a['missing'] for sid in lib.schemas for a in call_actions(lib, sid, ctx, g))):
            return 1
        if g.op == 'Imp':
            return 1 + cost(ctx + (g.args[0],), g.args[1])
        if g.op == 'And':
            return 1 + cost(ctx, g.args[0]) + cost(ctx, g.args[1])
        return 1 if any((t.op == 'Imp' and t.args[1] == g for t in ctx)) else 3
    return sum((cost(ctx, g) for ctx, g in state))

def generalize_plan(path, mapping):
    out = []
    for action in path:
        a = {k: v for k, v in action.items() if k not in ('missing', 'known_count')}
        if 'alpha' in a:
            a['alpha'] = {'slot_' + k: d(abstract_expr(v, mapping)) for k, v in a['alpha'].items()}
        if 'known' in a:
            a['known'] = {str(k): v for k, v in a['known'].items()}
        out.append(a)
    return out

def emit(lib, plan, target, topalpha=None, base=False):
    ctx = []
    goals = [(ctx, target)]
    lines = []
    indent = 2
    counter = 0
    trace = []

    def fresh():
        nonlocal counter
        counter += 1
        return 'h' + str(counter)
    for action in plan:
        ctx, g = goals.pop(0)
        before = {'context': [(n, d(t)) for n, t in ctx], 'goal': d(g)}
        sid = action['id']
        a = dict(action)
        if a['kind'] == 'CALL_F':
            alpha = {k.removeprefix('slot_'): substitute(fromdata(v), topalpha) for k, v in a['alpha'].items()}
            pre, res = split(lib.schemas[sid])
            concrete = [substitute(p, alpha) for p in pre]
            if substitute(res, alpha) != g:
                raise ValueError('whole-F codomain mismatch')
            known = {int(k): v for k, v in a['known'].items()}
            for i, h in known.items():
                if ctx[h][1] != concrete[i]:
                    raise ValueError('whole-F hypothesis slot mismatch')
            args = ' '.join(('(slot_' + k + ' := ' + render(v) + ')' for k, v in alpha.items()))
            args += ' ' + ' '.join((f'(premise_{i} := {ctx[h][0]})' for i, h in known.items()))
            missing = [t for i, t in enumerate(concrete) if i not in known]
            lines.append(' ' * indent + ('apply ' if missing else 'exact ') + f'lib_{sid} ' + args)
            goals[0:0] = [(ctx.copy(), t) for t in missing]
            runtime = {'proposition_slots': {k: d(v) for k, v in alpha.items()}, 'hypothesis_slots': known}
        elif sid == 'f_imp_intro':
            if g.op != 'Imp':
                raise ValueError('intro incompatible')
            n = fresh()
            ctx = ctx + [(n, g.args[0])]
            goals.insert(0, (ctx, g.args[1]))
            lines.append(' ' * indent + f'intro {n}')
            runtime = {'new_hypothesis': n}
        elif sid == 'f_exact':
            i = a.get('hyp', next((j for j, (_, t) in enumerate(ctx) if t == g), -1))
            if i < 0 or ctx[i][1] != g:
                raise ValueError('exact binding mismatch')
            lines.append(' ' * indent + 'exact ' + ctx[i][0])
            runtime = {'hypothesis_index': i}
        elif sid == 'f_imp_apply':
            i = a.get('hyp', next((j for j, (_, t) in enumerate(ctx) if t.op == 'Imp' and t.args[1] == g), -1))
            if i < 0 or ctx[i][1].op != 'Imp' or ctx[i][1].args[1] != g:
                raise ValueError('apply binding mismatch')
            lines.append(' ' * indent + 'apply ' + ctx[i][0])
            goals.insert(0, (ctx, ctx[i][1].args[0]))
            runtime = {'hypothesis_index': i}
        elif sid == 'f_and_construct':
            if g.op != 'And':
                raise ValueError('constructor mismatch')
            lines.append(' ' * indent + 'constructor')
            goals[0:0] = [(ctx.copy(), g.args[0]), (ctx.copy(), g.args[1])]
            runtime = {}
        elif sid == 'f_and_cases':
            i = a.get('hyp', next((j for j, (_, t) in enumerate(ctx) if t.op == 'And'), -1))
            if i < 0 or ctx[i][1].op != 'And':
                raise ValueError('cases mismatch')
            n, t = ctx[i]
            left, right = (fresh(), fresh())
            lines.extend([' ' * indent + f'cases {n} with', ' ' * indent + f'| intro {left} {right} =>'])
            indent += 4
            ctx = ctx[:i] + ctx[i + 1:] + [(left, t.args[0]), (right, t.args[1])]
            goals.insert(0, (ctx, g))
            runtime = {'hypothesis_index': i, 'goal_unchanged': d(g)}
        else:
            raise ValueError('unknown operator')
        trace.append({'operator': sid, 'kind': action['kind'], 'before': before, 'execution_alpha': runtime, 'remaining_goals': [d(t) for _, t in goals]})
    if goals:
        raise ValueError('plan incomplete')
    return (lines, trace)

def theorem(name, target, lines):
    names = sorted({e.args[0] for e in walk(target) if e.op == 'Atom'})
    return f'theorem {name} (' + ' '.join(names) + ' : Prop) : ' + render(target) + ' := by\n' + '\n'.join(lines)

def exact_proof(lib, sid, alpha, target, name):
    args = ' '.join(('(slot_' + k + ' := ' + render(v) + ')' for k, v in alpha.items()))
    pre, _ = split(lib.schemas[sid])
    lines = [f'  intro h{i}' for i in range(len(pre))]
    args += ' ' + ' '.join((f'(premise_{i} := h{i})' for i in range(len(pre))))
    lines.append(f'  exact lib_{sid} ' + args)
    return theorem(name, target, lines)
