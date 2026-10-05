"""Single lifecycle-aware source for executable and formation search pools."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .public_operations import compatible_operations, is_first_class_operation, normalize_operation
from .serialization import thaw


LIFECYCLE_VIEWS = frozenset(("ACTIVE", "FULL", "RETIRED"))


@dataclass(frozen=True)
class RecipeOperation:
    """Minimal identity/level/capability view used by stored formation recipes."""
    identity: str
    level: int
    capability: str


@dataclass(frozen=True)
class TopologyRecipe:
    """F-resident recipe data, separate from executable public operations."""
    identity: str
    level: int
    recipe: Mapping


@dataclass(frozen=True)
class RecipeBasis:
    operations: tuple[RecipeOperation, ...]
    topologies: tuple[TopologyRecipe, ...]


class OperationPoolProvider:
    """Own Library eligibility, lifecycle exposure, and contract filtering.

    Composer receives PublicOperation values from ``get_operations``. The
    separate recipe basis is restricted to capability labels and F-resident
    topology recipes needed by the existing residual composition grammar.
    """

    def __init__(self, library, usage_registry=None, *, public_contracts=None,
                 operation_source=None):
        self.library = library
        self.operation_source = operation_source if operation_source is not None else library
        self.public_contracts = dict(public_contracts or {})
        self.usage_registry = usage_registry
        if usage_registry is not None and usage_registry.library is not library:
            raise ValueError("operation pool and UsageRegistry must share the same Library")

    def with_public_contracts(self, public_contracts):
        return OperationPoolProvider(self.library, self.usage_registry,
            public_contracts=public_contracts,operation_source=self.operation_source)

    def with_operation_source(self, operation_source):
        return OperationPoolProvider(self.library,self.usage_registry,
            public_contracts=self.public_contracts,operation_source=operation_source)

    def _known_ids(self):
        return tuple(item.identity for item in self.library)

    def persistent_ids(self):
        return frozenset(self._known_ids())

    def contains_id(self, structure_id):
        return structure_id in self.persistent_ids()

    def _lifecycle_ids(self, view):
        if view not in LIFECYCLE_VIEWS:
            raise ValueError("lifecycle_view must be ACTIVE, FULL, or RETIRED")
        known = frozenset(self._known_ids())
        if self.usage_registry is None:
            return known if view in ("ACTIVE", "FULL") else frozenset()
        if view == "FULL":
            return known
        if view == "ACTIVE":
            return self.usage_registry.active_ids().intersection(known)
        return self.usage_registry.retired_ids().intersection(known)

    @staticmethod
    def _ordered_ids(source_ids, allowed_ids):
        if source_ids is None:
            return None
        return tuple(dict.fromkeys(sid for sid in source_ids if sid in allowed_ids))

    def get_operations(self, contract=None, *, lifecycle_view="ACTIVE",
                       operation_ids=None, excluded_ids=(), extra_operations=()):
        """Return only executable public operations exposed in the requested view."""
        allowed = self._lifecycle_ids(lifecycle_view)
        excluded = set(excluded_ids)
        requested = self._ordered_ids(operation_ids, allowed)
        requested_set = None if requested is None else set(requested)
        by_id = {}
        for item in self.operation_source:
            sid = item.identity
            if sid not in allowed or sid in excluded or (requested_set is not None and sid not in requested_set):
                continue
            try:
                interface = normalize_operation(item, self.public_contracts.get(sid))
            except (KeyError, TypeError, ValueError):
                continue
            if is_first_class_operation(interface):
                by_id[sid] = interface

        # Uncommitted candidates remain a Composer feature, not persistent F.
        # They have no lifecycle status and therefore enter only ACTIVE/FULL.
        if lifecycle_view != "RETIRED":
            for raw in extra_operations:
                try:
                    interface = normalize_operation(raw, self.public_contracts.get(
                        raw.get("structure_id", raw.get("identity")) if isinstance(raw, Mapping) else None))
                except (KeyError, TypeError, ValueError, AttributeError):
                    continue
                sid = interface.identity
                if (not sid or sid in excluded or sid in allowed
                        or (requested_set is not None and sid not in requested_set)
                        or not is_first_class_operation(interface)):
                    continue
                by_id.setdefault(sid, interface)

        if requested is None:
            ordered = [by_id[sid] for sid in (item.identity for item in self.operation_source) if sid in by_id]
            ordered.extend(interface for sid, interface in by_id.items()
                           if sid not in allowed)
        else:
            ordered = [by_id[sid] for sid in requested if sid in by_id]
            ordered.extend(interface for sid, interface in by_id.items()
                           if sid not in allowed)
        if contract is not None:
            ordered = compatible_operations(ordered, contract)
        return tuple(ordered)

    def get_search_structures(self, domain, *, lifecycle_view="ACTIVE", structure_ids=None):
        """Return domain-audited structures for framework planners, in library order."""
        allowed = self._lifecycle_ids(lifecycle_view)
        requested = None if structure_ids is None else set(structure_ids)
        result = []
        for structure in self.library:
            if structure.identity not in allowed:
                continue
            if requested is not None and structure.identity not in requested:
                continue
            if domain.audit_structure(structure):
                result.append(structure)
        return tuple(result)

    def get_search_partition(self, domain, structure_ids):
        """Lifecycle-partition a domain's eligible structures for ordered fallback."""
        eligible = tuple(dict.fromkeys(structure_ids))
        active = self.get_search_structures(domain, lifecycle_view="ACTIVE",
                                            structure_ids=eligible)
        retired = self.get_search_structures(domain, lifecycle_view="RETIRED",
                                             structure_ids=eligible)
        if hasattr(self.library, "equivalence_class"):
            classes = {self.library.equivalence_class(item.identity) for item in (*active, *retired)}
        else:
            classes = {(item.identity,) for item in (*active, *retired)}
        return active, retired, len(classes)

    def get_recipe_basis(self, *, lifecycle_view="ACTIVE"):
        """Return the active F-resident composition recipe basis.

        This path is used only after direct public operation edges have been
        yielded. Recipe templates are data, not executable public operations.
        """
        if not hasattr(self.library, "_structures"):
            return RecipeBasis((), ())
        allowed = self._lifecycle_ids(lifecycle_view)
        operations = []
        topologies = []
        for structure in self.library:
            if structure.identity not in allowed:
                continue
            capability = structure.metadata.get("exec_capability")
            if capability:
                operations.append(RecipeOperation(structure.identity, structure.level, str(capability)))
            if structure.metadata.get("role") == "topology":
                raw_recipe = thaw(structure.kernel).get("formation_recipe")
                if isinstance(raw_recipe, dict) and raw_recipe.get("materialization"):
                    topologies.append(TopologyRecipe(structure.identity, structure.level, raw_recipe))
        return RecipeBasis(tuple(operations), tuple(topologies))

    def levels_for(self, structure_ids):
        """Read hierarchy levels for already exposed/persistent IDs only."""
        ids = tuple(dict.fromkeys(structure_ids))
        return {sid: self.library[sid].level for sid in ids if sid in self.library._structures}

    def lifecycle_summary(self):
        active = self._lifecycle_ids("ACTIVE")
        retired = self._lifecycle_ids("RETIRED")
        if hasattr(self.library, "equivalence_classes"):
            classes = len(self.library.equivalence_classes())
        else:
            classes = len(self._known_ids())
        return {"active_ids": active, "retired_ids": retired,
                "full_ids": self._lifecycle_ids("FULL"), "equivalence_classes": classes}
