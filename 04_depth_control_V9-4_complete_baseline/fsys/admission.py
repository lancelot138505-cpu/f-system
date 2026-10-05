"""Common persistent admission checks, independent of formation source."""
from .structure import Structure
from .serialization import digest, thaw

REQUIRED_TOKEN_CHECKS=('interface','domain_audit','executable','examples','randomized_oracle',
                       'cross_language','cold_instance','x_leakage','formation_parents')

_EXECUTION_REFERENCE_KEYS={'f','body_f','left_f','right_f'}


def _ordered_execution_refs(node):
    found=[]
    def visit(value):
        if isinstance(value,dict):
            for key,child in value.items():
                if key in _EXECUTION_REFERENCE_KEYS and isinstance(child,str):found.append(child)
                visit(child)
        elif isinstance(value,(list,tuple)):
            for child in value:visit(child)
    visit(node)
    return tuple(dict.fromkeys(found))


def _formation_path_rows(path):
    if not path:return ()
    if isinstance(path,dict):return (path,)
    if isinstance(path,(list,tuple)):
        return tuple(row for row in path if isinstance(row,dict))
    return ()


def audit_formation_parents(structure,library,formation_path=None):
    """Audit that a newly admitted composite names its actual persistent inputs.

    F-resident recipes are checked against the topology F and the F identities
    called by the executable. Other composites retain the canonical-compose
    contract, with any explicit formation-path child list checked verbatim.
    Legacy snapshots are loaded through Library._commit_verified and are not
    silently reinterpreted as new admissions.
    """
    kernel=thaw(structure.kernel)
    recipe=kernel.get('formation_recipe') if isinstance(kernel,dict) else None
    metadata=thaw(structure.metadata)
    observed=tuple(structure.parents)
    errors=[]
    if structure.level==0:
        if observed or structure.formation_provenance.parents:
            errors.append('primitive leaf must not declare formation parents')
        return {'passed':not errors,'issues':tuple(errors),'expected_parents':(),
                'observed_parents':observed,'topology_id':None,'direct_calls':()}
    if structure.formation_provenance.parents!=observed:
        errors.append('formation provenance parents differ from Structure.parents')
    if not observed:
        errors.append('composite formation has no persistent parents')
    missing=[parent for parent in observed if parent not in library._structures]
    if missing:
        errors.append('formation parents are not persistent: '+', '.join(missing))

    expected=None
    topology_id=None
    calls=()
    if isinstance(recipe,dict):
        topology_id=recipe.get('topology_id')
        if not isinstance(topology_id,str) or not topology_id:
            errors.append('formation_recipe is missing topology_id')
        elif topology_id not in library._structures:
            errors.append('formation topology is not persistent: '+topology_id)
        calls=_ordered_execution_refs(kernel.get('executable',{}))
        if not calls:
            errors.append('formation_recipe executable contains no child F references')
        expected=tuple(dict.fromkeys(((topology_id,) if topology_id else ())+calls))
        if observed!=expected:
            errors.append('Structure.parents do not exactly match topology plus executable child F references')
        bindings=recipe.get('operation_bindings')
        if not isinstance(bindings,dict) or not bindings:
            errors.append('formation_recipe operation_bindings are missing')
        else:
            values=tuple(value for value in bindings.values() if isinstance(value,str))
            if len(values)!=len(bindings) or set(values)!=set(calls) or len(set(values))!=len(values):
                errors.append('formation_recipe operation_bindings do not exactly match executable child F references')
    else:
        call_plan=metadata.get('call_plan')
        planned=()
        if isinstance(call_plan,(list,tuple)):
            if all(isinstance(item,str) for item in call_plan):
                planned=tuple(dict.fromkeys(call_plan))
            elif all(isinstance(item,dict) and isinstance(item.get('id'),str) for item in call_plan):
                planned=tuple(dict.fromkeys(item['id'] for item in call_plan))
        if planned:
            expected=planned
            if observed!=expected:
                errors.append('Structure.parents do not exactly match metadata.call_plan formation inputs')
        else:
            calls=_ordered_execution_refs(kernel.get('executable',{}))
            if calls:
                expected=calls
                if observed!=expected:
                    errors.append('Structure.parents do not exactly match executable F references')

    rows=_formation_path_rows(formation_path)
    provenance_details=thaw(structure.formation_provenance.details)
    if not rows and isinstance(provenance_details,dict):
        rows=_formation_path_rows(provenance_details.get('formation_path'))
    if rows:
        final=rows[-1]
        declared=final.get('parents_at_formation',final.get('children',final.get('parents')))
        if declared is not None and tuple(declared)!=observed:
            errors.append('formation_path parent list differs from Structure.parents')
        if isinstance(recipe,dict):
            path_topology=final.get('topology_f',final.get('topology_id'))
            if path_topology!=topology_id:
                errors.append('formation_path topology differs from formation_recipe topology_id')
            path_materialization=final.get('materialization')
            if recipe.get('materialization') is not None and path_materialization!=recipe.get('materialization'):
                errors.append('formation_path materialization differs from formation_recipe')
            path_bindings=final.get('operation_bindings')
            if path_bindings is not None and path_bindings!=recipe.get('operation_bindings'):
                errors.append('formation_path operation_bindings differ from formation_recipe')

    return {'passed':not errors,'issues':tuple(errors),'expected_parents':expected,
            'observed_parents':observed,'topology_id':topology_id,'direct_calls':calls}


def require_formation_parents(structure,library,formation_path=None):
    result=audit_formation_parents(structure,library,formation_path)
    if not result['passed']:
        raise ValueError('formation parent admission failed: '+'; '.join(result['issues']))
    return result

def subject_digest(structure):
    return digest({'parents':structure.parents,'level':structure.level,'kernel':structure.kernel,
                   'ports':[p.to_dict() for p in structure.ports],'alpha_schema':structure.alpha_schema.to_dict(),
                   'metadata':{key:value for key,value in structure.metadata.items()
                               if key not in ('knowledge_status','operation_identity','consolidation_provenance')}})

def validate_structure(structure):
    restored=Structure.from_dict(structure.to_dict())
    if digest(restored.to_dict())!=digest(structure.to_dict()):
        raise ValueError('structure serialization roundtrip failed')
    contract=structure.metadata.get('public_operation_interface')
    if contract:
        from .public_operations import normalize_operation,is_first_class_operation
        if not is_first_class_operation(normalize_operation(structure)):
            raise ValueError('public operation interface failed admission')

def require_token_evidence(structure,verification):
    meta=structure.metadata
    token=any(meta.get(key) for key in ('translation_primitive','translation_rule','translation_dispatch'))
    token=token or bool(meta.get('exec_capability')) or str(meta.get('scope','')).startswith('algorithm')
    token=token or structure.operation_id.startswith(('token.','algorithm.','exec.generic.','candidate.')) or meta.get('role')=='topology'
    if not token:return
    if meta.get('exec_capability')=='composed_algorithm' and not meta.get('public_operation_interface'):
        raise ValueError('new token root requires its complete published public interface')
    checks=getattr(verification,'admission_checks',None) or {}
    missing=[key for key in REQUIRED_TOKEN_CHECKS if checks.get(key) is not True]
    if missing:raise ValueError('token admission checks missing or failed: '+', '.join(missing))
    if not verification.source_digest or verification.admission_subject_digest!=subject_digest(structure):
        raise ValueError('token admission receipt is not bound to the candidate')
