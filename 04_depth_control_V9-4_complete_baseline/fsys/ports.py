from dataclasses import dataclass, field
from .serialization import FrozenMap

@dataclass(frozen=True)
class Port:
    name: str
    direction: str
    dtype: str
    role: str = "value"
    cardinality: int | None = 1
    constraints: FrozenMap = field(default_factory=FrozenMap)
    def __post_init__(self):
        if self.direction not in ("input", "output"): raise ValueError("invalid port direction")
        if self.cardinality is not None and self.cardinality < 1: raise ValueError("invalid cardinality")
        object.__setattr__(self, "constraints", FrozenMap(self.constraints))
    def to_dict(self):
        from .serialization import thaw
        return {**vars(self), "constraints": thaw(self.constraints)}
