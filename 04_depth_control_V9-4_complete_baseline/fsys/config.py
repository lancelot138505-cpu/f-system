from dataclasses import dataclass

@dataclass(frozen=True)
class ArchConfig:
    slot_capacity: int = 8
    max_level: int = 6
    search_width: int = 64
    max_depth: int = 11
    expansion_budget: int = 1800
    z_depth: int = 1
    z_width: int = 1
    z_dim: int = 1
    def __post_init__(self):
        if min(vars(self).values()) < 1: raise ValueError("architecture capacities must be positive")

@dataclass(frozen=True)
class BackendConfig:
    default: str = "cpu"
    alpha_width: str = "cpu"
    def __post_init__(self):
        if self.default != "cpu": raise ValueError("structural computation is CPU-first")
        if self.alpha_width not in ("cpu", "cuda", "auto"): raise ValueError("unknown alpha backend")

@dataclass(frozen=True)
class OptimizationConfig:
    updates: int = 80
    schedule_steps: int = 80
    seed: int = 830
    learning_rate: float = .18
    temperature_start: float = 1.
    temperature_end: float = .15
    repeated_penalty: bool = True
    early_stop: bool = False
    check_every: int = 10
    patience: int = 2
    epsilon: float = 1e-5
    def __post_init__(self):
        if self.updates<0 or min(self.schedule_steps,self.check_every,self.patience)<1:raise ValueError("invalid update schedule")
        if min(self.learning_rate,self.temperature_start,self.temperature_end)<=0 or self.epsilon<0:raise ValueError("invalid numeric optimization parameter")
