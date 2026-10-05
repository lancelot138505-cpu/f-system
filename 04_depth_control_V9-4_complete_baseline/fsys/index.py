"""CPU index derived solely from committed Structure; query state is never stored in knowledge."""
class StructureIndex:
    def __init__(self, library, domain):
        self.library,self.domain=library,domain;self.version=-1;self.buckets={};self.build_seconds=0.
    def rebuild(self):
        import time
        st=time.perf_counter();buckets={}
        for s in self.library:
            for key in self.domain.index_keys(s):buckets.setdefault(key,[]).append(s.identity)
        self.buckets={k:tuple(sorted(v)) for k,v in buckets.items()};self.version=self.library.version
        self.build_seconds=time.perf_counter()-st
    def query(self, state):
        if self.version!=self.library.version:self.rebuild()
        found=set()
        for key in self.domain.index_queries(state):found.update(self.buckets.get(key,()))
        return tuple(sorted(found))
