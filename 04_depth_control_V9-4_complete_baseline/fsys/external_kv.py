"""JSON-backed external store for newly formed composite F candidates."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json

STATUSES={'CANDIDATE','VALIDATED','PROMOTABLE','PERSISTENT','REJECTED','EXPIRED'}
TRANSITIONS={
 'CANDIDATE':{'VALIDATED','REJECTED','EXPIRED'},
 'VALIDATED':{'PROMOTABLE','REJECTED','EXPIRED'},
 'PROMOTABLE':{'PERSISTENT','REJECTED','EXPIRED'},
 'PERSISTENT':set(),'REJECTED':set(),'EXPIRED':set()}

class ExternalCandidateKV:
    FORMAT='fsys-external-kv-1'
    def __init__(self,path=None):
        self.path=Path(path) if path else None;self.records={};self.events=[]
    def add(self,candidate_id,structure,*,operation_signature,operation_identity_if_known=None,
            formation_path=(),formation_source='goal_driven_search',task_signature='',goal_signature='',
            slot_schema=(),binding_schema=(),candidate_graph=None,formation_cost=0.0,search_nodes=0,candidate_rank=0,
            x_leakage_status='PENDING',independent_formation_count=1):
        if candidate_id in self.records:raise ValueError('duplicate candidate id')
        now=datetime.now(timezone.utc).isoformat()
        record={'candidate_id':candidate_id,'structure':structure.to_dict(),'operation_signature':operation_signature,
          'operation_identity_if_known':operation_identity_if_known,'status':'CANDIDATE','parents':list(structure.parents),
          'formation_path':list(formation_path),'formation_depth':structure.level,'formation_source':formation_source,
          'task_signature':task_signature,'goal_signature':goal_signature,'slot_schema':list(slot_schema),
          'binding_schema':list(binding_schema),'candidate_graph':candidate_graph if candidate_graph is not None else dict(structure.kernel),
          'verification_receipts':[],'verification_pass_count':0,'verification_fail_count':0,
          'independent_formation_count':independent_formation_count,'reuse_count':0,'equivalent_candidates':[],
          'formation_cost':formation_cost,'execution_cost':0.0,'search_nodes':search_nodes,'candidate_rank':candidate_rank,
          'x_leakage_status':x_leakage_status,'promotion_evidence':[],'promotion_reason':None,
          'consolidation_provenance':None,'created_at':now,'updated_at':now,'version':1}
        self.records[candidate_id]=record;self._event('CANDIDATE_CREATED',candidate_id,{'parents':structure.parents});self._flush_journal();return record
    def _event(self,event,candidate_id,details=None):
        self.events.append({'event':event,'candidate_id':candidate_id,'details':details or {},'at':datetime.now(timezone.utc).isoformat()})
    def transition(self,candidate_id,status,*,receipt=None,reason=None,evidence=None):
        r=self.records[candidate_id]
        if status not in TRANSITIONS[r['status']]:raise ValueError(f"invalid candidate transition {r['status']} -> {status}")
        old=r['status'];r['status']=status;r['updated_at']=datetime.now(timezone.utc).isoformat();r['version']+=1
        if receipt is not None:
            r['verification_receipts'].append(receipt)
            cases=int(receipt.get('cases',1))
            if receipt.get('passed'):r['verification_pass_count']+=cases
            else:r['verification_fail_count']+=cases
        if reason:r['promotion_reason']=reason
        if evidence:r['promotion_evidence'].append(evidence)
        self._event('STATUS_'+status,candidate_id,{'from':old,'reason':reason,'receipt':receipt,'evidence':evidence});self._flush_journal();return r
    def add_receipt(self,candidate_id,receipt):
        r=self.records[candidate_id];r['verification_receipts'].append(receipt)
        cases=int(receipt.get('cases',1))
        if receipt.get('passed'):r['verification_pass_count']+=cases
        else:r['verification_fail_count']+=cases
        r['updated_at']=datetime.now(timezone.utc).isoformat();r['version']+=1;self._event('VERIFICATION_RECEIPT',candidate_id,receipt);self._flush_journal();return r
    def update(self,candidate_id,**values):
        r=self.records[candidate_id];r.update(values);r['updated_at']=datetime.now(timezone.utc).isoformat();r['version']+=1
        self._event('RECORD_UPDATED',candidate_id,values);self._flush_journal();return r
    def get(self,candidate_id):return self.records[candidate_id]
    def _flush_journal(self):
        if not self.path:return
        journal=self.path.with_suffix('.journal.jsonl');journal.parent.mkdir(parents=True,exist_ok=True)
        with journal.open('a',encoding='utf-8') as f:f.write(json.dumps(self.events[-1],ensure_ascii=False,separators=(',',':'))+'\n')
    def save(self,path=None):
        dest=Path(path) if path else self.path
        if not dest:raise ValueError('KV path is required')
        dest.parent.mkdir(parents=True,exist_ok=True)
        temp=dest.with_suffix(dest.suffix+'.tmp')
        temp.write_text(json.dumps({'format':self.FORMAT,'count':len(self.records),'records':list(self.records.values()),'events':self.events},ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(dest);self.path=dest;return dest
    @classmethod
    def load(cls,path):
        raw=json.loads(Path(path).read_text(encoding='utf-8'))
        if raw.get('format')!=cls.FORMAT:raise ValueError('unsupported External KV format')
        kv=cls(path);kv.records={r['candidate_id']:r for r in raw['records']};kv.events=list(raw.get('events',[]));return kv
    def summary(self):
        lifecycle={name:sum(e['event']==event for e in self.events) for name,event in {
          'candidate_created':'CANDIDATE_CREATED','candidate_validated':'STATUS_VALIDATED','candidate_promotable':'STATUS_PROMOTABLE',
          'candidate_promoted':'STATUS_PERSISTENT','candidate_rejected':'STATUS_REJECTED','candidate_expired':'STATUS_EXPIRED'}.items()}
        return {'format':self.FORMAT,'count':len(self.records),'status_counts':{s:sum(r['status']==s for r in self.records.values()) for s in sorted(STATUSES)},
          'lifecycle_event_counts':lifecycle,
          'verification_passes':sum(r['verification_pass_count'] for r in self.records.values()),
          'verification_failures':sum(r['verification_fail_count'] for r in self.records.values()),
          'independent_formations':sum(r['independent_formation_count'] for r in self.records.values()),
          'reuse_count':sum(r['reuse_count'] for r in self.records.values()),'event_count':len(self.events)}
