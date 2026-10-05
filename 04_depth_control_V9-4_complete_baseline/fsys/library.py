from .structure import Structure
from .serialization import digest, save_json, load_json, freeze

class Library:
    def __init__(self):
        self._structures = {}; self._version = 0; self._equivalence = [];self._hash_cache=None;self._equivalence_cache=None;self._equivalence_lookup=None
    def __iter__(self): return iter(self._structures.values())
    def __len__(self): return len(self._structures)
    def __getitem__(self, sid): return self._structures[sid]
    @property
    def version(self): return self._version
    @property
    def equivalence(self):return tuple(self._equivalence)
    @property
    def max_level(self):return max((s.level for s in self),default=0)
    def structures_at_level(self,level):return tuple(s for s in self if s.level==level)
    def hierarchy(self):return tuple({'id':s.identity,'parents':s.parents,'level':s.level,'ancestors':self.ancestors(s.identity),'primitives':self.primitives(s.identity)} for s in self)
    @property
    def persistent_hash(self):
        if self._hash_cache is None:self._hash_cache=digest(self.to_dict())
        return self._hash_cache
    def commit(self, structure, verification, *, formation_path=None):
        from .admission import require_token_evidence, require_formation_parents
        require_token_evidence(structure,verification)
        require_formation_parents(structure,self,formation_path)
        return self._commit_verified(structure,verification)
    def _commit_verified(self, structure, verification):
        if not verification or not verification.passed: raise ValueError("verification gate failed before commit")
        if not verification.verifier:raise ValueError("verification receipt needs a verifier identity")
        # Origin is deliberately absent from the admission policy.
        from .admission import validate_structure
        validate_structure(structure)
        if structure.identity in self._structures: raise ValueError("identity already committed")
        if any(p not in self._structures for p in structure.parents): raise ValueError("missing parent")
        level = 0 if not structure.parents else 1+max(self[p].level for p in structure.parents)
        if structure.level != level: raise ValueError("hierarchy level mismatch")
        if structure.identity in structure.parents: raise ValueError("cyclic parent")
        if not structure.formation_provenance.verifier:
            from dataclasses import replace
            from .provenance import FormationProvenance
            structure=replace(structure,formation_provenance=FormationProvenance(structure.formation_provenance.source,
                              structure.parents,verification.verifier,verification.source_digest,structure.formation_provenance.details))
        if structure.formation_provenance.parents!=structure.parents:raise ValueError("formation parent provenance mismatch")
        self._structures[structure.identity] = structure; self._version += 1;self._hash_cache=None;self._equivalence_cache=None;self._equivalence_lookup=None
        return structure
    def ancestors(self, sid):
        found=set()
        def visit(n):
            for p in self[n].parents:
                if p not in found: found.add(p); visit(p)
        visit(sid); return tuple(sorted(found))
    def primitives(self, sid):
        s=self[sid]
        return (sid,) if not s.parents else sum((self.primitives(p) for p in s.parents), ())
    def record_equivalence(self, a, b, evidence):
        if a == b or a not in self._structures or b not in self._structures: raise ValueError("equivalence requires distinct known identities")
        self._equivalence.append(freeze({"a":a,"b":b,"evidence":evidence}))
        self._version += 1;self._hash_cache=None;self._equivalence_cache=None;self._equivalence_lookup=None
    def equivalence_classes(self):
        """Return the deterministic connected-component partition, including singletons."""
        if self._equivalence_cache is None:
            parent={sid:sid for sid in self._structures}
            def find(sid):
                while parent[sid]!=sid:
                    parent[sid]=parent[parent[sid]];sid=parent[sid]
                return sid
            def union(a,b):
                ra,rb=find(a),find(b)
                if ra!=rb:
                    lo,hi=sorted((ra,rb));parent[hi]=lo
            for edge in self._equivalence:union(edge['a'],edge['b'])
            groups={}
            for sid in sorted(parent):groups.setdefault(find(sid),[]).append(sid)
            self._equivalence_cache=tuple(sorted((tuple(sorted(v)) for v in groups.values()),key=lambda c:c[0]))
            self._equivalence_lookup={sid:members for members in self._equivalence_cache for sid in members}
        return self._equivalence_cache
    def equivalence_class(self, structure_id):
        if structure_id not in self._structures:raise KeyError(structure_id)
        self.equivalence_classes()
        return self._equivalence_lookup[structure_id]
    def are_equivalent(self, a, b):
        if a not in self._structures or b not in self._structures:return False
        return self.equivalence_class(a)==self.equivalence_class(b)
    def to_dict(self):
        return {"format":"fsys-9.0", "version":self._version,
                "structures":[s.to_dict() for s in sorted(self,key=lambda s:(s.level,s.identity))],
                "behavioral_equivalence":self._equivalence}
    def save(self, path):
        body=self.to_dict(); save_json(path,{"library":body,"hash":digest(body)})
    @classmethod
    def load(cls, path):
        from .verifier import VerificationRecord
        raw=load_json(path); body=raw["library"]
        if body.get('format')!='fsys-9.0':raise ValueError('unsupported persistence format')
        if digest(body)!=raw["hash"]: raise ValueError("persistent hash mismatch")
        lib=cls()
        for data in body["structures"]:
            s=Structure.from_dict(data)
            if not s.formation_provenance.verifier: raise ValueError("unverified provenance")
            lib._commit_verified(s,VerificationRecord(True,"verified snapshot",s.formation_provenance.verification_digest))
        lib._equivalence=[freeze(e) for e in body["behavioral_equivalence"]];lib._version=body["version"]
        return lib
