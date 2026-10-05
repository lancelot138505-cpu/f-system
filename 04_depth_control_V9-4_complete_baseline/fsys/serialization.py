"""Persistent data are recursively immutable, JSON-compatible, and runtime-free."""
import hashlib
import json
import math
from collections.abc import Mapping

FORBIDDEN = {"X", "runtime_X", "runtime_alpha", "alpha_vector", "decoded_binding", "structural_binding",
             "candidate_ids", "candidate_id", "candidate_map", "temporary_substitution", "search_state", "gpu_tensor_cache"}

class FrozenMap(Mapping):
    __slots__ = ("_items",)
    def __init__(self, values=()):
        items = tuple(values.items() if isinstance(values, Mapping) else values)
        if FORBIDDEN.intersection(str(k) for k,v in items):raise ValueError("runtime fields are forbidden in persistent data")
        object.__setattr__(self, "_items", tuple(sorted((str(k), freeze(v)) for k, v in items)))
    def __getitem__(self, key):
        for k, v in self._items:
            if k == key: return v
        raise KeyError(key)
    def __iter__(self): return (k for k, _ in self._items)
    def __len__(self): return len(self._items)
    def __setattr__(self, *args): raise TypeError("persistent mapping is immutable")
    def __repr__(self): return repr(dict(self._items))

def freeze(value):
    if isinstance(value, FrozenMap): return value
    if isinstance(value, Mapping):
        if FORBIDDEN.intersection(value): raise ValueError("runtime fields are forbidden in persistent data")
        return FrozenMap(value)
    if isinstance(value, (list, tuple)): return tuple(freeze(v) for v in value)
    if value is None or isinstance(value, (str, bool, int)): return value
    if isinstance(value, float) and math.isfinite(value): return value
    raise TypeError(f"not persistent JSON data: {type(value).__name__}")

def thaw(value):
    if isinstance(value, Mapping): return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)): return [thaw(v) for v in value]
    return value

def digest(value):
    return hashlib.sha256(json.dumps(thaw(value), sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()

def save_json(path, value):
    from pathlib import Path
    Path(path).write_text(json.dumps(thaw(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

def load_json(path):
    from pathlib import Path
    return json.loads(Path(path).read_text(encoding="utf-8"))
