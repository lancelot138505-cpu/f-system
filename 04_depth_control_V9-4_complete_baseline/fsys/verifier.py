from abc import ABC, abstractmethod
from dataclasses import dataclass
import hashlib
import subprocess
import time
from pathlib import Path

@dataclass(frozen=True)
class VerificationRecord:
    passed: bool
    verifier: str
    source_digest: str = ""
    elapsed_seconds: float = 0.
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    admission_checks: dict | None = None
    admission_subject_digest: str = ""

class Verifier(ABC):
    @abstractmethod
    def verify(self, artifact, context=None): ...

class NoOpVerifier(Verifier):
    def verify(self, artifact, context=None): return VerificationRecord(True, "noop", elapsed_seconds=0.)

class RuleVerifier(Verifier):
    def __init__(self, rule=None): self.rule = rule
    def verify(self, artifact, context=None):
        st = time.perf_counter()
        passed = self.rule(artifact) if self.rule else context.domain.verify_artifact(artifact)
        from .serialization import digest
        evidence=digest({'value_repr':repr(artifact.value),'checks_repr':repr(artifact.checks)})
        return VerificationRecord(bool(passed), "rule",evidence, elapsed_seconds=time.perf_counter()-st)

class LeanVerifier(Verifier):
    def __init__(self, directory=None, lake=None, timeout=120):
        self.directory = Path(directory or "fsys_verification").resolve()
        self.lake = str(lake or Path.home()/".elan/bin/lake.exe"); self.timeout = timeout
    def verify(self, artifact, context=None):
        import re
        if not artifact.source or re.search(r"\b(?:sorry|aesop|simp|omega|linarith)\b", artifact.source):
            return VerificationRecord(False, "lean", stderr="missing or forbidden proof source")
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory/"lean-toolchain").write_text("leanprover/lean4:v4.15.0\n", encoding="utf-8")
        (self.directory/"lakefile.lean").write_text("import Lake\nopen Lake DSL\npackage fsys_verification\n", encoding="utf-8")
        h = hashlib.sha256(artifact.source.encode()).hexdigest()
        file = self.directory/("Verify_"+h[:16]+".lean"); file.write_text(artifact.source, encoding="utf-8")
        st = time.perf_counter()
        try:
            p = subprocess.run([self.lake,"env","lean",file.name], cwd=self.directory,
                               capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=self.timeout)
            return VerificationRecord(p.returncode==0,"lean",h,time.perf_counter()-st,p.returncode,p.stdout,p.stderr)
        except (OSError, subprocess.TimeoutExpired) as e:
            return VerificationRecord(False,"lean",h,time.perf_counter()-st,stderr=str(e))
