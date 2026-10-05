from dataclasses import dataclass, field
from .serialization import FrozenMap, thaw

@dataclass(frozen=True)
class FormationProvenance:
    source: str
    parents: tuple[str, ...] = ()
    verifier: str = ""
    verification_digest: str = ""
    details: FrozenMap = field(default_factory=FrozenMap)
    def __post_init__(self):
        object.__setattr__(self, "parents", tuple(self.parents))
        object.__setattr__(self, "details", FrozenMap(self.details))
    def to_dict(self): return {**vars(self), "details": thaw(self.details)}

@dataclass
class ExecutionProvenance:
    structure_id: str | None
    backend: str
    status: str
    details: dict = field(default_factory=dict)
