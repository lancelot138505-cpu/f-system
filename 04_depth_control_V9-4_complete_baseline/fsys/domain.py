from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class StructuralMatch:
    exact: bool
    binding: dict
    residual: object = None
    score: float = 0.

@dataclass
class Action:
    structure_id: str
    payload: object

@dataclass
class Artifact:
    value: object = None
    source: str | None = None
    checks: tuple = ()
    scope: str = "execution"

class DomainAdapter(ABC):
    name = "abstract"
    @abstractmethod
    def validate_instance(self, X): ...
    @abstractmethod
    def state_type(self, X): ...
    @abstractmethod
    def candidate_space(self, X): ...
    def candidate_count(self, X): return len(self.candidate_space(X))
    @abstractmethod
    def port_compatible(self, a, b): ...
    @abstractmethod
    def decode_alpha(self, structure, X, alpha): ...
    @abstractmethod
    def execute_leaf(self, structure, X, alpha, context): ...
    @abstractmethod
    def structural_signature(self, structure): ...
    @abstractmethod
    def structural_exact(self, structure, X, context=None): ...
    @abstractmethod
    def structural_partial(self, structure, X, context=None): ...
    @abstractmethod
    def abstract_instance(self, X, plan, context): ...
    def execute_structure(self, structure, X, alpha, context):
        if context is not None and hasattr(context,"traversed_structures"):
            context.traversed_structures.append(structure.identity)
        return self.execute_leaf(structure, X, alpha, context) if not structure.parents else self.execute_composite(structure, X, alpha, context)
    def execute_composite(self, structure, X, alpha, context): raise NotImplementedError
    def prepare_numeric_batch(self, structures, X, context=None): raise NotImplementedError("no numeric WIDTH representation in this domain")
    def countermodel(self, X): return None
    def audit_structure(self, structure): return True
    def retrieval_candidates(self, library): return list(library)
    def prefilter(self, structures, X): return list(structures)
    def index_keys(self, structure): return (("all",),)
    def index_queries(self, state): return (("all",),)
    def composition_definition(self,left,right,wiring):raise NotImplementedError("domain must define explicit wiring semantics")
