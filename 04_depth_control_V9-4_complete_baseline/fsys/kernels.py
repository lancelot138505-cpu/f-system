from abc import ABC, abstractmethod
from .serialization import FrozenMap

class LeafKernel(ABC):
    @abstractmethod
    def execute(self, X, alpha, context): ...
    @abstractmethod
    def formation_parameters(self): ...
    @abstractmethod
    def freeze(self): ...
    @abstractmethod
    def audit(self): ...

class CustomKernel(LeafKernel):
    def __init__(self, reference, parameters=None):
        self.reference, self.parameters = str(reference), FrozenMap(parameters or {})
    def execute(self, X, alpha, context): return context.domain.execute_kernel(self.reference, self.parameters, X, alpha, context)
    def formation_parameters(self): return self.parameters
    def freeze(self): return FrozenMap({"reference": self.reference, "parameters": self.parameters})
    def audit(self): return {"instance_free": True, "reference": self.reference}

class ZAKernel(LeafKernel):
    """Optional leaf implementation; dimensions are capacity, fitted A/z are formation content."""
    def __init__(self, z_dim=1):
        if z_dim<1:raise ValueError('kernel capacity must be positive')
        self.z_dim = z_dim; self._parameters = None
    def fit(self, inputs, outputs):
        if self._parameters is not None: raise RuntimeError("kernel already frozen")
        # Small formation kernels use QR in Python; no second OpenMP/BLAS runtime.
        import math
        def row(v):
            try:return [float(t) for t in v]
            except TypeError:return [float(v)]
        x=[row(v) for v in inputs];y=[row(v) for v in outputs]
        if not x or len(x)!=len(y) or any(len(r)!=self.z_dim for r in x):raise ValueError('formation data shape mismatch')
        outdim=len(y[0])
        if outdim<1 or any(len(r)!=outdim for r in y) or not all(math.isfinite(t) for r in x+y for t in r):raise ValueError('invalid formation values')
        cols=[[r[j] for r in x] for j in range(self.z_dim)];Q=[];R=[[0.]*self.z_dim for _ in range(self.z_dim)]
        for j,c in enumerate(cols):
            v=list(c)
            for i,q in enumerate(Q):
                R[i][j]=math.fsum(a*b for a,b in zip(q,v));v=[a-R[i][j]*b for a,b in zip(v,q)]
            R[j][j]=math.sqrt(math.fsum(a*a for a in v))
            if R[j][j]<1e-12:raise ValueError('rank-deficient kernel formation data')
            Q.append([a/R[j][j] for a in v])
        A=[[0.]*outdim for _ in range(self.z_dim)]
        for output in range(outdim):
            rhs=[math.fsum(q[i]*y[i][output] for i in range(len(x))) for q in Q]
            for j in range(self.z_dim-1,-1,-1):A[j][output]=(rhs[j]-math.fsum(R[j][t]*A[t][output] for t in range(j+1,self.z_dim)))/R[j][j]
        self._parameters = FrozenMap({"A": A, "z_dim": self.z_dim})
        return self
    def formation_parameters(self):
        if self._parameters is None: raise RuntimeError("kernel has no formed parameters")
        return self._parameters
    def freeze(self): return FrozenMap({"reference": "za.linear", "parameters": self.formation_parameters()})
    def execute(self, X, alpha, context): return context.domain.execute_kernel("za.linear", self.formation_parameters(), X, alpha, context)
    def audit(self): return {"instance_free": True, "formed": self._parameters is not None, "capacity": self.z_dim}
