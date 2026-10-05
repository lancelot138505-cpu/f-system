import unittest
from dataclasses import replace
from pathlib import Path
from fsys.library import Library
from fsys.admission import REQUIRED_TOKEN_CHECKS,subject_digest
from fsys.provenance import FormationProvenance
from fsys.verifier import VerificationRecord
from fsys.external_kv import ExternalCandidateKV
from fsys.promotion import PromotionGate
from fsys.preference import UsageRegistry
from fsys.serialization import digest,thaw


class TestTokenAdmission(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]
        self.lib=Library.load(root/'artifacts'/'v94_unseen_instance_growth'/'library_final.json')
        old=self.lib['F_dcfedcf1d26444553cdb']
        from fsys.token_goal_domain import recursive_operation_contract
        self.obj=replace(old,structure_id='F_admission_test',operation_id='admission.test',
            metadata={**dict(old.metadata),'public_operation_interface':recursive_operation_contract(old.alpha_schema.binding_slots)})

    def receipt(self,checks=None,subject=None):
        return VerificationRecord(True,'unit admission verifier',digest({'test':'gate'}),
            admission_checks=checks or {name:True for name in REQUIRED_TOKEN_CHECKS},
            admission_subject_digest=subject or subject_digest(self.obj))

    def test_each_missing_or_failed_stage_blocks_every_origin_without_mutation(self):
        before=self.lib.persistent_hash
        for source in ('ordinary composition','autonomous discovery','unknown source'):
            obj=replace(self.obj,formation_provenance=FormationProvenance(source,self.obj.parents))
            for stage in REQUIRED_TOKEN_CHECKS:
                with self.subTest(source=source,stage=stage):
                    checks={name:name!=stage for name in REQUIRED_TOKEN_CHECKS}
                    with self.assertRaises(ValueError):self.lib.commit(obj,self.receipt(checks))
                    self.assertEqual(before,self.lib.persistent_hash)

    def test_positive_pass_flag_alone_is_not_full_validation(self):
        with self.assertRaises(ValueError):self.lib.commit(self.obj,VerificationRecord(True,'schema-only'))

    def test_receipt_for_different_payload_is_rejected(self):
        with self.assertRaises(ValueError):self.lib.commit(self.obj,self.receipt(subject='different-candidate'))

    def test_complete_evidence_enters_common_commit(self):
        self.lib.commit(self.obj,self.receipt())
        self.assertIn(self.obj.identity,self.lib._structures)

    def test_recipe_parent_mismatch_is_rejected_in_common_commit(self):
        wrong_parents=tuple(reversed(self.obj.parents))
        bad=replace(self.obj,parents=wrong_parents,
            formation_provenance=FormationProvenance('ordinary composition',wrong_parents))
        before=self.lib.persistent_hash
        with self.assertRaisesRegex(ValueError,'formation parent admission failed'):
            self.lib.commit(bad,self.receipt(subject=subject_digest(bad)))
        self.assertEqual(before,self.lib.persistent_hash)

    def promotion_store(self):
        kv=ExternalCandidateKV();kv.add('candidate',self.obj,operation_signature=self.obj.operation_id,
            formation_path=thaw(self.obj.formation_provenance.details).get('formation_path',[]),candidate_graph=thaw(self.obj.kernel),
            slot_schema=self.obj.alpha_schema.binding_slots,
            independent_formation_count=2,x_leakage_status='ZERO')
        receipt={'passed':True,'cases':4,'verifier':'unit verifier','receipt_digest':'unit-receipt',
            'validation_stage':'cold_instance','admission_subject_digest':subject_digest(self.obj),
            'admission_checks':{name:True for name in REQUIRED_TOKEN_CHECKS}}
        kv.transition('candidate','VALIDATED',receipt=receipt)
        return kv,PromotionGate(self.lib,kv,UsageRegistry(self.lib))

    def test_pending_leakage_and_insufficient_independent_formations_block_promotion(self):
        for change in ({'x_leakage_status':'PENDING'},{'independent_formation_count':1}):
            kv,gate=self.promotion_store();kv.update('candidate',**change)
            before=self.lib.persistent_hash
            with self.assertRaises(ValueError):gate.promote('candidate',operation_identity=self.obj.operation_id,
                min_verified_cases=4,min_cold_instance_cases=4,independent_formations=2)
            self.assertEqual(before,self.lib.persistent_hash)
            self.assertEqual(kv.get('candidate')['status'],'VALIDATED')

    def test_warm_receipt_cannot_claim_cold_validation(self):
        kv,gate=self.promotion_store()
        kv.get('candidate')['verification_receipts'][0]['validation_stage']='warm'
        with self.assertRaises(ValueError):gate.promote('candidate',operation_identity=self.obj.operation_id,
            min_verified_cases=4,min_cold_instance_cases=0,independent_formations=2)

    def test_full_promotion_passes(self):
        kv,gate=self.promotion_store()
        obj=gate.promote('candidate',operation_identity=self.obj.operation_id,min_verified_cases=4,
            min_cold_instance_cases=4,independent_formations=2)
        self.assertEqual(obj.identity,self.obj.identity)
        self.assertEqual(kv.get('candidate')['status'],'PERSISTENT')
