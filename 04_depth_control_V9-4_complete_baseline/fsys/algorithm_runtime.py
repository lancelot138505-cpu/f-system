"""Generic source-free algorithm graph renderers used by the scale experiment.

Algorithm knowledge is persisted as a small language-neutral statement/expression
graph.  This module contains only generic graph traversal and target syntax rules.
"""
from __future__ import annotations

import json
from collections.abc import Mapping


def rename_graph(value, names):
    """Alpha-rename variable references in a graph without touching literals."""
    if isinstance(value, (list, tuple)):
        return [rename_graph(x, names) for x in value]
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if key in ("var", "set", "for", "update") and isinstance(item, str):
                result[key] = names.get(item, item)
            elif key == "set" and isinstance(item, (list, tuple)):
                result[key] = [names.get(x, x) for x in item]
            elif key == "args" and isinstance(item, list):
                result[key] = [names.get(x, x) if isinstance(x, str) else rename_graph(x, names) for x in item]
            else:
                result[key] = rename_graph(item, names)
        return result
    return value


def expr(e, lang):
    if "var" in e: return e["var"]
    if "lit" in e: return json.dumps(e["lit"], ensure_ascii=False)
    if "array" in e: return "[" + ", ".join(expr(x, lang) for x in e["array"]) + "]"
    if "index" in e: return f"{expr(e['index'], lang)}[{expr(e['at'], lang)}]"
    if "call" in e:
        name = e["call"]
        args = ", ".join(expr(x, lang) for x in e.get("args", []))
        if name == "len": return (f"({expr(e['args'][0], lang)}).length" if lang == "js" else f"len({args})")
        if name == "append":
            obj, item = e["args"]
            return (f"{expr(obj, lang)}.push({expr(item, lang)})" if lang == "js" else f"{expr(obj, lang)}.append({expr(item, lang)})")
        if name == "copy":
            obj = expr(e["args"][0], lang)
            return f"{obj}.slice()" if lang == "js" else f"{obj}[:]"
        raise ValueError(f"unknown generic call {name}")
    if "un" in e:
        op = "!" if lang == "js" and e["un"] == "not" else ("not " if e["un"] == "not" else e["un"])
        return f"({op}{expr(e['arg'], lang)})"
    if "op" in e:
        a, b = e["args"]
        op = e["op"]
        if op == "//": return f"Math.floor({expr(a, lang)} / {expr(b, lang)})" if lang == "js" else f"({expr(a, lang)} // {expr(b, lang)})"
        if op == "and": op = "&&" if lang == "js" else "and"
        if op == "or": op = "||" if lang == "js" else "or"
        if lang == "js" and op == "==": op = "==="
        if lang == "js" and op == "!=": op = "!=="
        return f"({expr(a, lang)} {op} {expr(b, lang)})"
    raise ValueError(f"unknown expression graph: {e!r}")


def _suite(body, lang, depth):
    return "\n".join(stmt(s, lang, depth) for s in body)


def stmt(s, lang, depth=1):
    pad = "    " * depth
    if "set" in s:
        target = lvalue(s["set"], lang)
        if isinstance(target, list): target = "[" + ", ".join(target) + "]"
        return pad + f"{target} = {expr(s['value'], lang)}"
    if "update" in s:
        return pad + f"{s['update']} {s['op']} {expr(s['value'], lang)}"
    if "if" in s:
        head = pad + f"if ({expr(s['if'], lang)}) {{" if lang == "js" else pad + f"if {expr(s['if'], lang)}:"
        then = _suite(s.get("then", []), lang, depth + 1) or ("    " * (depth + 1) + "pass" if lang == "py" else "")
        if lang == "js":
            text = head + "\n" + then + "\n" + pad + "}"
            if s.get("else"): text += " else {\n" + _suite(s["else"], lang, depth + 1) + "\n" + pad + "}"
            return text
        text = head + "\n" + then
        if s.get("else"): text += "\n" + pad + "else:\n" + _suite(s["else"], lang, depth + 1)
        return text
    if "while" in s:
        head = pad + f"while ({expr(s['while'], lang)}) {{" if lang == "js" else pad + f"while {expr(s['while'], lang)}:"
        body = _suite(s.get("body", []), lang, depth + 1) or ("    " * (depth + 1) + "pass" if lang == "py" else "")
        return head + "\n" + body + ("\n" + pad + "}" if lang == "js" else "")
    if "for" in s:
        var = s["for"]
        start, stop = expr(s["start"], lang), expr(s["stop"], lang)
        if lang == "js": head = pad + f"for (let {var} = {start}; {var} < {stop}; {var}++) {{"
        else: head = pad + f"for {var} in range({start}, {stop}):"
        body = _suite(s.get("body", []), lang, depth + 1) or ("    " * (depth + 1) + "pass" if lang == "py" else "")
        return head + "\n" + body + ("\n" + pad + "}" if lang == "js" else "")
    if "return" in s: return pad + "return " + expr(s["return"], lang) + (";" if lang == "js" else "")
    if s.get("break"): return pad + ("break;" if lang == "js" else "break")
    if s.get("continue"): return pad + ("continue;" if lang == "js" else "continue")
    if "expr" in s: return pad + expr(s["expr"], lang) + (";" if lang == "js" else "")
    raise ValueError(f"unknown statement graph: {s!r}")


def lvalue(target, lang):
    if isinstance(target, str): return target
    if isinstance(target, Mapping) and "index" in target:
        return f"{expr(target['index'], lang)}[{expr(target['at'], lang)}]"
    if isinstance(target, (list, tuple)): return [lvalue(x, lang) for x in target]
    raise ValueError(f"unknown assignment target graph: {target!r}")


def render_function(name, args, body, lang):
    if lang == "js":
        return f"function {name}({', '.join(args)}) {{\n{_suite(body, 'js', 1)}\n}}\n"
    return f"def {name}({', '.join(args)}):\n{_suite(body, 'py', 1)}\n"
