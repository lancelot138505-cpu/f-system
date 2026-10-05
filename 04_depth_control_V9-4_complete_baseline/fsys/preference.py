"""Runtime search preferences derived only from verified successful traversals."""
from collections import Counter
from pathlib import Path
from .serialization import load_json, save_json


class UsageRegistry:
    FORMAT = "fsys-9.0-usage"

    def __init__(self, library):
        self.library = library
        self._counts = {}
        self._current = {}
        self._synced_library_version = -1
        self._dirty = True
        self.active_switches_total = 0
        self.last_active_switches = 0
        self._sync_classes()

    @staticmethod
    def _key(members):
        return members[0]

    def _sync_classes(self):
        if not self._dirty and self._synced_library_version == self.library.version:
            return self._current
        classes = self.library.equivalence_classes()
        old = self._current
        old_active_by_member = {sid: active for group, active in old.items() for sid in group}
        new = {}
        switches = 0
        for members in classes:
            members = tuple(members)
            previous = sorted({old_active_by_member[sid] for sid in members if sid in old_active_by_member})
            maximum = max((self._counts.get(sid, 0) for sid in members), default=0)
            tied = tuple(sid for sid in members if self._counts.get(sid, 0) == maximum)
            retained = sorted(set(previous).intersection(tied))
            active = retained[0] if retained else tied[0]
            if previous and active not in previous:
                switches += 1
            new[members] = active
        self._current = new
        self._synced_library_version = self.library.version
        self._dirty = False
        self.last_active_switches = switches
        self.active_switches_total += switches
        return new

    def count(self, structure_id):
        if structure_id not in self.library._structures:
            raise KeyError(structure_id)
        return int(self._counts.get(structure_id, 0))

    def counts(self):
        return {sid: self.count(sid) for sid in sorted(self.library._structures)}

    def active_member(self, equivalence_class):
        members = tuple(sorted(equivalence_class))
        if not members or any(sid not in self.library._structures for sid in members):
            raise ValueError("known nonempty equivalence class required")
        self._sync_classes()
        for group, active in self._current.items():
            if group == members:
                return active
        raise ValueError("members do not form a complete equivalence class")

    def status(self, structure_id):
        members = self.library.equivalence_class(structure_id)
        return "ACTIVE" if self.active_member(members) == structure_id else "RETIRED"

    def partition_ids(self, structure_ids):
        """Split a candidate sequence into preferred and deferred IDs, preserving order."""
        ids = tuple(dict.fromkeys(sid for sid in structure_ids if sid in self.library._structures))
        self._sync_classes()
        active = {sid for group, sid in self._current.items()}
        seen = {self.library.equivalence_class(sid) for sid in ids}
        preferred = tuple(sid for sid in ids if sid in active)
        retired = tuple(sid for sid in ids if sid not in active)
        return preferred, retired, len(seen)

    def active_ids(self):
        self._sync_classes()
        return frozenset(self._current.values())

    def retired_ids(self):
        active = self.active_ids()
        return frozenset(self.library._structures).difference(active)

    def record_successful_path(self, structure_ids, verification):
        """Count each visited node only when paired with a passing verification receipt."""
        if not getattr(verification,"passed",False):
            raise ValueError("usage updates require a passed verification receipt")
        visits = tuple(structure_ids)
        increments = Counter(visits)
        unknown = set(increments).difference(self.library._structures)
        if unknown:
            raise KeyError(f"successful path contains unknown Structures: {sorted(unknown)}")
        self._sync_classes()
        for sid, n in increments.items():
            self._counts[sid] = self._counts.get(sid, 0) + n
        self._dirty = True
        self._sync_classes()
        return {"visited": list(visits), "increments": dict(sorted(increments.items())),
                "active_switches": self.last_active_switches}

    def snapshot(self):
        self._sync_classes()
        return {"format": self.FORMAT, "library_hash": self.library.persistent_hash,
                "counts": self.counts(),
                "preferred": {self._key(group): active for group, active in sorted(self._current.items())}}

    def save(self, path):
        save_json(path, self.snapshot())
        return Path(path)

    @classmethod
    def load(cls, path, library):
        raw = load_json(path)
        if raw.get("format") != cls.FORMAT:
            raise ValueError("unsupported usage sidecar format")
        if raw.get("library_hash") != library.persistent_hash:
            raise ValueError("usage sidecar library hash mismatch")
        known = set(library._structures)
        counts = raw.get("counts", {})
        if not isinstance(counts, dict) or set(counts).difference(known):
            raise ValueError("usage sidecar contains unknown Structure IDs")
        for value in counts.values():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("usage counts must be nonnegative integers")
        registry = cls(library)
        registry._counts = dict(counts)
        saved = raw.get("preferred", {})
        if not isinstance(saved, dict):
            raise ValueError("usage sidecar preferred map must be an object")
        by_key = {cls._key(members): members for members in library.equivalence_classes()}
        restored = {}
        for key, active in saved.items():
            members = by_key.get(key)
            if members is None or active not in members:
                raise ValueError("usage sidecar has an invalid preferred member")
            maximum = max(registry.count(sid) for sid in members)
            if registry.count(active) != maximum:
                raise ValueError("saved preferred member is not usage-maximal")
            restored[members] = active
        registry._current.update(restored)
        registry._dirty = True
        registry._sync_classes()
        registry.last_active_switches=0;registry.active_switches_total=0
        return registry
