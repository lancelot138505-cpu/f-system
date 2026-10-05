from dataclasses import fields
from .structure import Structure
from .serialization import FORBIDDEN,thaw
def persistent_audit(library,domain):
    allowed={'structure_id','operation_id','ports','alpha_schema','parents','level','formation_provenance','kernel','metadata'}
    def clean(value):
        if isinstance(value,dict):return not FORBIDDEN.intersection(value) and all(clean(v) for v in value.values())
        if isinstance(value,list):return all(clean(v) for v in value)
        return value is None or isinstance(value,(str,int,float,bool))
    rows=[{'id':s.identity,'allowed_fields':{f.name for f in fields(s)}==allowed,'json_runtime_free':clean(thaw(s.to_dict())),
           'domain_instance_free':domain.audit_structure(s),'verified_provenance':bool(s.formation_provenance.verifier),
           'hierarchy_level_valid':s.level==(0 if not s.parents else 1+max(library[p].level for p in s.parents))} for s in library]
    return {'passed':all(all(v for k,v in row.items() if k!='id') for row in rows),'structures':rows,'library_hash':library.persistent_hash}
