from abc import ABC, abstractmethod
from dataclasses import dataclass
import math

@dataclass(frozen=True)
class AlphaSchema:
    binding_slots: tuple[str, ...] = ()
    continuous_dims: int = 0
    routing_dims: int = 0
    def __post_init__(self):
        object.__setattr__(self, "binding_slots", tuple(self.binding_slots))
        if min(self.continuous_dims, self.routing_dims) < 0: raise ValueError("negative dimension")
        if len(set(self.binding_slots)) != len(self.binding_slots): raise ValueError("duplicate slots")
    def dimension(self, candidate_count):
        return self.continuous_dims + len(self.binding_slots)*candidate_count + self.routing_dims
    def to_dict(self): return vars(self)

@dataclass
class AlphaTensor:
    values: tuple[float, ...]
    mask: tuple[bool, ...]
    shape: tuple[int, ...]
    solver: str = "external"
    def __post_init__(self):
        self.values = tuple(float(v) for v in self.values)
        self.mask = tuple(bool(v) for v in self.mask)
        self.shape=tuple(self.shape)
        if len(self.values) != len(self.mask): raise ValueError("mask size mismatch")
        if not all(math.isfinite(v) for v in self.values): raise ValueError("nonfinite alpha")
        if math.prod(self.shape) != len(self.values): raise ValueError("alpha shape mismatch")
    @property
    def active_count(self): return sum(self.mask)

class AlphaSolver(ABC):
    @abstractmethod
    def solve(self, structure, X, context=None): ...
    def solve_batch(self, structures, X, context=None):
        return [self.solve(s, X, context) for s in structures]

class AlphaOptimizer(AlphaSolver):
    def fit(self, structure, instance, context=None): return self.solve(structure, instance, context)
    def fit_batch(self, structures, instance, context=None): return self.solve_batch(structures, instance, context)
    def fit_state(self, structure, state, context=None):
        raise NotImplementedError("structural DEPTH does not run Adam")

class DirectStructuralSolver(AlphaSolver):
    def __init__(self, domain): self.domain = domain
    def solve(self, structure, X, context=None):
        match = self.domain.structural_exact(structure, X, context)
        if not match.exact: return None
        return self.domain.encode_binding(structure, X, match.binding, solver="direct_structural")

class ContinuousAdamSolver(AlphaOptimizer):
    def __init__(self, domain, backend=None, config=None):
        from .backend import CPUBackend
        from .config import OptimizationConfig
        self.domain, self.backend, self.config = domain, backend or CPUBackend(), config or OptimizationConfig()
    def solve(self, structure, X, context=None):
        return self.solve_batch([structure], X, context)[0]
    def solve_batch(self, structures, X, context=None):
        if not structures:return []
        batch = self.domain.prepare_numeric_batch(structures, X, context)
        result = self.backend.alpha_backend().compute(batch, self.config, top_k=len(structures))
        out = [None]*len(structures)
        for i, a in zip(result.indices, result.alphas):
            out[int(i)] = self.domain.alpha_from_batch(structures[int(i)], X, a, batch, solver="continuous_adam")
        return out

class OneShotAlphaPredictor(AlphaSolver):
    def solve(self, *args, **kwargs): raise NotImplementedError("reserved interface; no predictor in V9.0")

class DifferentialEvolutionSolver(AlphaSolver):
    def solve(self, *args, **kwargs): raise NotImplementedError("reserved interface")
