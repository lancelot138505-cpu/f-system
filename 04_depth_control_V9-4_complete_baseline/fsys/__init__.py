"""fsys: explicit structural computation; CPU-first, optional numeric WIDTH acceleration."""
from .config import ArchConfig,BackendConfig,OptimizationConfig
from .structure import Structure,Leaf,Composite
from .ports import Port
from .alpha import AlphaSchema,AlphaTensor,AlphaOptimizer,AlphaSolver,ContinuousAdamSolver,DirectStructuralSolver,OneShotAlphaPredictor,DifferentialEvolutionSolver
from .library import Library
from .preference import UsageRegistry
from .operation_pool import OperationPoolProvider
from .domain import DomainAdapter
from .retrieval import RetrievalStrategy,SymbolicRetrieval,ContinuousRetrieval,HybridRetrieval,AdaptiveRetrieval
from .search import DepthPlanner
from .formation import FormationEngine,canonical_compose
from .public_operations import (PROTOCOL_VERSION as PUBLIC_OPERATION_PROTOCOL,
    PublicOperation,normalize_operation,is_first_class_operation,
    compatible_operations,bind_operation)
from .runtime import ExecutionContext,Engine
from .provenance import FormationProvenance,ExecutionProvenance
from .verifier import Verifier,NoOpVerifier,RuleVerifier,LeanVerifier
from .backend import Backend,CPUBackend,CUDABackend,CandidateParallelAlphaBackend
from .cost import CostPolicy
from .index import StructureIndex
from .kernels import LeafKernel,ZAKernel,CustomKernel
from .failures import FailureType,audit_retrieval
from .audit import persistent_audit
__version__="9.0.0"
