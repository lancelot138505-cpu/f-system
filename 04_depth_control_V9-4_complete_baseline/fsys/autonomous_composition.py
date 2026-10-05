"""Origin-neutral discovery of recurring child/topology compositions.

This module decides only which existing child sequence and topology recurred.
It never infers formal slots, ports, alpha schema, capability, or an operation.
"""
from __future__ import annotations

from collections import defaultdict

from .serialization import digest, thaw


class AutonomousCompositionDiscovery:
    def discover(self, successful_traces, *, minimum_occurrences=2):
        if minimum_occurrences < 2:
            raise ValueError("recurrence discovery requires at least two occurrences")
        occurrences = defaultdict(list)
        representatives = {}
        for trace_index, trace in enumerate(successful_traces):
            if not trace.get("success", False):
                continue
            for composition in trace.get("compositions", ()):
                children = tuple(composition.get("children", ()))
                topology = thaw(composition.get("topology"))
                if not children or topology is None:
                    continue
                key = digest({"children": children, "topology": topology})
                representatives[key] = (children, topology)
                # Observed bindings, alpha values, and trace-local values are
                # deliberately ignored. They cannot affect slot existence.
                occurrences[key].append(trace_index)
        proposals = []
        for key in sorted(occurrences):
            indexes = occurrences[key]
            if len(indexes) < minimum_occurrences:
                continue
            children, topology = representatives[key]
            proposals.append({"children": children, "topology": topology})
        return proposals
