"""Simple, auditable promotion gate for composite F candidates in External KV."""
from __future__ import annotations
from dataclasses import replace
from .provenance import FormationProvenance
from .structure import Structure
from .serialization import digest
from .verifier import VerificationRecord

class PromotionGate:
    def __init__(self,library,kv,usage):self.library=library;self.kv=kv;self.usage=usage;self.receipts=[]
    def promote(self,candidate_id,*,operation_identity,min_verified_cases,min_cold_instance_cases=0,independent_formations=1,
                x_leakage_status='ZERO',executable=True,reason='randomized oracle gate'):
        r=self.kv.get(candidate_id)
        if min_verified_cases<1 or min_cold_instance_cases<0:raise ValueError('invalid verification thresholds')
        if r['status']!='VALIDATED':raise ValueError('promotion requires VALIDATED candidate')
        passed=sum(int(x.get('cases',1)) for x in r['verification_receipts'] if x.get('passed'))
        failed=sum(int(x.get('cases',1)) for x in r['verification_receipts'] if not x.get('passed'))
        if passed<min_verified_cases or failed:raise ValueError('verification evidence does not meet promotion gate')
        cold_passed=sum(int(x.get('cases',1)) for x in r['verification_receipts'] if x.get('passed') and x.get('validation_stage')=='cold_instance')
        if cold_passed<min_cold_instance_cases:raise ValueError('independent cold-instance validation evidence does not meet promotion gate')
        if x_leakage_status!='ZERO' or r['x_leakage_status']!='ZERO':raise ValueError('X leakage gate failed')
        if not executable:raise ValueError('candidate is not executable')
        if independent_formations<1 or r['independent_formation_count']<independent_formations:raise ValueError('formation provenance incomplete')
        parents=r['parents']
        if any(parent not in self.library._structures for parent in parents):raise ValueError('all F parents must be persistent before promotion')
        if not r.get('formation_source') or not r.get('formation_path'):raise ValueError('formation provenance is incomplete')
        old=Structure.from_dict(r['structure'])
        if tuple(parents)!=old.parents:raise ValueError('candidate parent record disagrees with structure')
        from .admission import audit_formation_parents
        formation_audit=audit_formation_parents(old,self.library,r.get('formation_path'))
        if not formation_audit['passed']:
            raise ValueError('formation parent admission failed: '+'; '.join(formation_audit['issues']))
        if tuple(r['slot_schema'])!=old.alpha_schema.binding_slots:raise ValueError('candidate formal slot record disagrees with structure')
        if any(not x.get('verifier') or not x.get('receipt_digest') or int(x.get('cases',1))<1 for x in r['verification_receipts']):
            raise ValueError('verification receipt is incomplete')
        from .admission import REQUIRED_TOKEN_CHECKS,subject_digest
        checks={name:(formation_audit['passed'] if name=='formation_parents' else any(x.get('passed') and x.get('admission_checks',{}).get(name) is True
                       and x.get('admission_subject_digest')==subject_digest(old)
                       and (name!='cold_instance' or x.get('validation_stage')=='cold_instance')
                       for x in r['verification_receipts'])) for name in REQUIRED_TOKEN_CHECKS}
        if any(s.operation_id==operation_identity for s in self.library):
            existing=next(s for s in self.library if s.operation_id==operation_identity)
            if digest(existing.kernel)==digest(old.kernel):
                raise ValueError('duplicate persistent operation must consolidate before new identity creation')
            raise ValueError('operation identity collision with a non-equivalent persistent F')
        receipt_data={'candidate_id':candidate_id,'operation_identity':operation_identity,'verified_cases':passed,
          'admission_checks':checks,'admission_subject_digest':subject_digest(old),
          'verification_failures':failed,'independent_formations':independent_formations,'x_leakage_status':'ZERO',
          'promotion_rule':{'minimum_verified_cases':min_verified_cases,'minimum_cold_instance_cases':min_cold_instance_cases,
            'cold_instance_cases_passed':cold_passed,'cold_instance_validation':cold_passed>=min_cold_instance_cases,'executable':True},
          'promotion_reason':reason,'consolidation':{'equivalence_check':'no existing persistent identity collision','provenance_merge':'none required'}}
        verification=VerificationRecord(True,'External KV Promotion Gate',digest(receipt_data),admission_checks=checks,
                                        admission_subject_digest=subject_digest(old))
        metadata={**dict(old.metadata),'operation_identity':operation_identity,'knowledge_status':'PERSISTENT',
                  'consolidation_provenance':receipt_data['consolidation']|{'verification_passes':passed,'independent_formations':independent_formations,
                    'reuse_count':r['reuse_count'],'promotion_rule':receipt_data['promotion_rule'],'x_leakage_status':'ZERO'}}
        persisted=replace(old,operation_id=operation_identity,metadata=metadata,
          formation_provenance=FormationProvenance(r['formation_source'],old.parents,verification.verifier,verification.source_digest,
            {'candidate_reference_digest':digest({'candidate_id':candidate_id}),'formation_path':r['formation_path'],'task_signature':r['task_signature']}))
        self.library.commit(persisted,verification,formation_path=r['formation_path'])
        self.kv.update(candidate_id,structure=persisted.to_dict(),operation_identity_if_known=operation_identity,
                       x_leakage_status='ZERO',consolidation_provenance=receipt_data['consolidation'])
        self.kv.transition(candidate_id,'PROMOTABLE',reason='promotion criteria satisfied',evidence=receipt_data)
        self.kv.transition(candidate_id,'PERSISTENT',reason=reason,evidence=receipt_data)
        self.receipts.append(receipt_data)
        return persisted
