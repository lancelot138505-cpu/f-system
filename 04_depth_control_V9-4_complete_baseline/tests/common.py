import json,unittest
from pathlib import Path
import fsys as fs
from fsys.domains import LeanPropDomain,NumericDomain,NumericProblem,parse
ROOT=Path(__file__).resolve().parents[1]
try:
    import torch
    torch.set_num_threads(1)
except ImportError:pass
def lean_engine(retrieval=None,backend=None,planner=None):
    d=LeanPropDomain();l=fs.Library.load(ROOT/'artifacts/base_library.json')
    return fs.Engine(d,l,retrieval=retrieval,backend=backend,planner=planner,verifier=fs.LeanVerifier(ROOT/'artifacts/test_lean'))
def numeric_engine():
    d=NumericDomain();return fs.Engine(d,d.bootstrap())
X='(A ∧ B) → (A ∧ B)'
