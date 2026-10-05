"""No AST/domain imports: optional accelerator sees only compact numeric batches."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
import time

@dataclass
class NumericFeatureBatch:
    packed: object
    mask: object
    shape: tuple[int,int,int]
    feature_seconds: float = 0.
    candidate_ids: tuple[str,...] = ()
    def initial_alpha(self, seed=830):
        import torch
        return torch.randn(self.shape,generator=torch.Generator().manual_seed(seed))*.2

@dataclass
class AlphaBatchResult:
    indices: object
    scores: object
    alphas: object
    timing: dict
    diagnostics: dict
    candidate_ids: tuple = ()

class Backend(ABC):
    name = "abstract"
    @abstractmethod
    def alpha_backend(self): ...

class CPUBackend(Backend):
    name="cpu"
    def alpha_backend(self):return CandidateParallelAlphaBackend("cpu")

class CUDABackend(Backend):
    name="cuda"
    @staticmethod
    def available():
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:return False
    def alpha_backend(self):return CandidateParallelAlphaBackend("cuda" if self.available() else "cpu")

class CandidateParallelAlphaBackend:
    """One packed H2D phase, numeric-only Adam, on-device TopK, one packed D2H result.

    Optional early stop checks only every configured interval, explicitly counted separately.
    """
    def __init__(self, device="cpu"):self.device=device
    def compute(self,batch,config,top_k=5,initial=None):
        import torch
        import numpy as np
        n,s,k=batch.shape;d=s*k;top_k=min(top_k,n)
        if n==0:raise ValueError("empty numeric batch")
        if top_k<1:raise ValueError("TopK must be positive")
        total_start=time.perf_counter();initial=batch.initial_alpha(config.seed) if initial is None else initial
        pack_start=time.perf_counter()
        features=torch.as_tensor(batch.packed,dtype=torch.float32)
        mask=torch.as_tensor(batch.mask,dtype=torch.float32).reshape(n,d)
        packed=torch.cat([features,initial.reshape(n,d),mask],1).contiguous()
        pack_seconds=time.perf_counter()-pack_start
        cuda=self.device=="cuda";timing={};synchronizations=0;early_transfers=0
        if cuda:
            torch.cuda.reset_peak_memory_stats()
            # CPU allocation/pinning stays outside GPU intervals.
            pin_start=time.perf_counter();packed=packed.pin_memory();pin_seconds=time.perf_counter()-pin_start
            events=[torch.cuda.Event(enable_timing=True) for _ in range(8)]
            events[0].record();devicebuf=packed.to("cuda",non_blocking=True);events[1].record()
        else:
            pin_seconds=0.;devicebuf=packed
        fwidth=features.shape[1]
        phi=devicebuf[:,:fwidth]
        alpha=devicebuf[:,fwidth:fwidth+d].reshape(n,s,k).clone().requires_grad_()
        active_mask=devicebuf[:,fwidth+d:].reshape(n,s,k).bool()
        # Layout: q[S,K], coverage[S,K], repeated[S], operator[1].
        q=phi[:,:d].reshape(n,s,k);coverage=phi[:,d:2*d].reshape(n,s,k)
        repeat=phi[:,2*d:2*d+s];op=phi[:,2*d+s]
        active=active_mask.any(-1)
        adam=torch.optim.Adam([alpha],lr=config.learning_rate)
        def objective(step):
            tau=config.temperature_start+(config.temperature_end-config.temperature_start)*min(step/config.schedule_steps,1.)
            p=torch.softmax(alpha.masked_fill(~active_mask,-1e9)/tau,-1)*active_mask
            occ=(p*q).sum((-1,-2))/active.sum(-1).clamp_min(1)
            cov=(p*coverage).sum((-1,-2));ce=-(q*torch.log(p.clamp_min(1e-9))).sum(-1)
            consistency=(ce*repeat).sum(-1)/repeat.sum(-1).clamp_min(1)
            context=occ;residual=1-occ
            score=.1*op+.3*occ+.2*cov+.2*context+.2*(1-residual)
            loss=-score+(.15*consistency if config.repeated_penalty else 0)
            return loss,score
        if cuda:events[2].record()
        opt_start=time.perf_counter();previous=None;stable=0;actual_updates=0
        for step in range(config.updates):
            loss,score=objective(step)
            adam.zero_grad();loss.sum().backward();adam.step();actual_updates+=1
            if config.early_stop and actual_updates%config.check_every==0:
                with torch.no_grad():
                    _,current=objective(actual_updates);vals,ids=torch.topk(current,top_k)
                    if previous is not None:
                        converged=(ids==previous[1]).all() & ((vals-previous[0]).abs().max()<config.epsilon)
                        stable=stable+1 if bool(converged.item()) else 0
                        if cuda:early_transfers+=1;synchronizations+=1
                    previous=(vals.detach(),ids.detach())
                    if stable>=config.patience:break
        with torch.no_grad():_,score=objective(actual_updates)
        opt_seconds=time.perf_counter()-opt_start
        if cuda:events[3].record();events[4].record()
        rank_start=time.perf_counter();scores,ids=torch.topk(score,top_k,sorted=True)
        selected=alpha.detach().reshape(n,d).index_select(0,ids)
        output=torch.cat([ids.to(torch.float32).unsqueeze(1),scores.unsqueeze(1),selected],1).contiguous()
        topk_seconds=time.perf_counter()-rank_start
        if cuda:
            events[5].record()
            host=torch.empty(output.shape,dtype=output.dtype,pin_memory=True)
            events[6].record();host.copy_(output,non_blocking=True);events[7].record()
            sync_start=time.perf_counter();torch.cuda.synchronize();sync_seconds=time.perf_counter()-sync_start;synchronizations+=1
            timing={"h2d_seconds":events[0].elapsed_time(events[1])/1000,
                    "optimization_seconds":events[2].elapsed_time(events[3])/1000,
                    "topk_seconds":events[4].elapsed_time(events[5])/1000,
                    "d2h_seconds":events[6].elapsed_time(events[7])/1000,"final_wait_seconds":sync_seconds}
            peak=torch.cuda.max_memory_allocated()
        else:
            host=output.detach();peak=0
            timing={"h2d_seconds":0.,"optimization_seconds":opt_seconds,"topk_seconds":topk_seconds,"d2h_seconds":0.,"final_wait_seconds":0.}
        arr=host.numpy().copy()
        timing.update(feature_cpu_seconds=batch.feature_seconds,pack_cpu_seconds=pack_seconds,pin_cpu_seconds=pin_seconds,
                      width_numeric_total_seconds=time.perf_counter()-total_start,
                      adam_dispatch_wall_seconds=opt_seconds)
        return AlphaBatchResult(arr[:,0].astype(np.int64),arr[:,1],arr[:,2:],timing,
                    {"device":self.device,"batch_shape":batch.shape,"active_elements":int(np.asarray(batch.mask).sum()),
                     "masked_fraction":1-float(np.asarray(batch.mask).mean()),"gpu_peak_allocated_bytes":peak,
                     "updates":actual_updates,"h2d_transfer_calls":int(cuda),"d2h_transfer_calls":int(cuda)+early_transfers,
                     "h2d_bytes":packed.numel()*packed.element_size() if cuda else 0,
                     "d2h_bytes":output.numel()*output.element_size()+early_transfers if cuda else 0,
                     "explicit_synchronizations":synchronizations,"launches":"separate profiler probe; not guessed from Adam steps",
                     "initialization":"CPU random normal; same seed/shape as frozen V8.4",
                     "default_returns_all_alpha":top_k==n,"early_stop":config.early_stop},
                    tuple(batch.candidate_ids[int(i)] for i in arr[:,0]) if batch.candidate_ids else ())
