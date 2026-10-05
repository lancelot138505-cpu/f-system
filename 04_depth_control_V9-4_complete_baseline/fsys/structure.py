from dataclasses import dataclass, field
from .ports import Port
from .alpha import AlphaSchema
from .provenance import FormationProvenance
from .serialization import FrozenMap, digest, thaw

@dataclass(frozen=True)
class Structure:
    structure_id: str
    operation_id: str
    ports: tuple[Port, ...] = ()
    alpha_schema: AlphaSchema = field(default_factory=AlphaSchema)
    parents: tuple[str, ...] = ()
    level: int = 0
    formation_provenance: FormationProvenance = field(default_factory=lambda: FormationProvenance("uncommitted"))
    kernel: FrozenMap = field(default_factory=FrozenMap)
    metadata: FrozenMap = field(default_factory=FrozenMap)
    def __post_init__(self):
        if not self.structure_id or not self.operation_id:raise ValueError("identity and operation must be nonempty")
        object.__setattr__(self, "ports", tuple(self.ports))
        object.__setattr__(self, "parents", tuple(self.parents))
        object.__setattr__(self, "kernel", FrozenMap(self.kernel))
        object.__setattr__(self, "metadata", FrozenMap(self.metadata))
        if self.level < 0 or bool(self.parents) != (self.level > 0): raise ValueError("invalid hierarchy shape")
        if len({p.name for p in self.ports}) != len(self.ports): raise ValueError("duplicate port")
        if isinstance(self, Leaf) and self.level != 0: raise ValueError("leaf has parents")
        if isinstance(self, Composite) and not self.parents: raise ValueError("composite needs parents")
    @property
    def identity(self): return self.structure_id
    @property
    def persistent_hash(self): return digest(self.to_dict())
    def execute(self, X, alpha, context): return context.domain.execute_structure(self, X, alpha, context)
    def compose(self, other, wiring, context):return context.engine.formation.compose(self,other,wiring,context)
    def to_dict(self):
        return {"kind": "leaf" if self.level == 0 else "composite", "structure_id": self.structure_id,
                "operation_id": self.operation_id, "ports": [p.to_dict() for p in self.ports],
                "alpha_schema": self.alpha_schema.to_dict(), "parents": self.parents, "level": self.level,
                "formation_provenance": self.formation_provenance.to_dict(),
                "kernel": thaw(self.kernel), "metadata": thaw(self.metadata)}
    @classmethod
    def from_dict(cls, data):
        data = dict(data); kind = data.pop("kind")
        if kind not in ('leaf','composite'):raise ValueError('unknown structure kind')
        data["ports"] = tuple(Port(**p) for p in data["ports"])
        data["alpha_schema"] = AlphaSchema(**data["alpha_schema"])
        data["formation_provenance"] = FormationProvenance(**data["formation_provenance"])
        return (Leaf if kind == "leaf" else Composite)(**data)

@dataclass(frozen=True)
class Leaf(Structure): pass

@dataclass(frozen=True)
class Composite(Structure): pass
