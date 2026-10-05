from dataclasses import dataclass
import json,hashlib,re
def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()

def unify(pattern, target, slots=None):
    """Recursive structural unification; each slot binds to an arbitrary Prop subtree."""
    slots = {} if slots is None else slots.copy()
    if pattern.op == 'Slot':
        name = pattern.args[0]
        if name in slots:
            return slots if slots[name] == target else None
        slots[name] = target
        return slots
    if pattern.op != target.op:
        return None
    if pattern.op == 'Atom':
        return slots if pattern.args == target.args else None
    for left, right in zip(pattern.args, target.args):
        slots = unify(left, right, slots)
        if slots is None:
            return None
    return slots

@dataclass(frozen=True)
class Prop:
    op: str
    args: tuple

    def to_data(self):
        return {'op': self.op, 'args': [a.to_data() if isinstance(a, Prop) else a for a in self.args]}

    def render(self):
        if self.op == 'Atom':
            return self.args[0]
        left, right = self.args
        return f'({left.render()} → {right.render()})' if self.op == 'Imp' else f'({left.render()} ∧ {right.render()})'

def Atom(n):
    return Prop('Atom', (n,))

def Imp(a, b):
    return Prop('Imp', (a, b))

def And(a, b):
    return Prop('And', (a, b))

def Slot(name):
    return Prop('Slot', (name,))

P=Prop
def parse(s):
    tokens = re.findall('[A-Za-z_][A-Za-z0-9_]*|[()∧→]', s)
    if ''.join(tokens) != re.sub('\\s', '', s):
        raise ValueError('unsupported token')
    pos = 0

    def unit():
        nonlocal pos
        t = tokens[pos]
        pos += 1
        if t == '(':
            e = arrow()
            if tokens[pos] != ')':
                raise ValueError('missing closing parenthesis')
            pos += 1
            return e
        if t in (')', '∧', '→'):
            raise ValueError('expected Atom')
        return Atom(t)

    def conj():
        nonlocal pos
        e = unit()
        while pos < len(tokens) and tokens[pos] == '∧':
            pos += 1
            e = And(e, unit())
        return e

    def arrow():
        nonlocal pos
        e = conj()
        if pos < len(tokens) and tokens[pos] == '→':
            pos += 1
            e = Imp(e, arrow())
        return e
    e = arrow()
    if pos != len(tokens):
        raise ValueError('trailing tokens')
    return e
