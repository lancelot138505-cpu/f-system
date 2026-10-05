from dataclasses import dataclass, field

@dataclass
class CostPolicy:
    mode: str = "cpu_only"
    history: dict = field(default_factory=dict)
    def __post_init__(self):
        if self.mode not in ('cpu_only','pure_symbolic','gpu_width','hybrid','adaptive'):raise ValueError('unknown cost policy')
    def observe(self,method,metrics,features=None):
        count=max(1,metrics.get('after',metrics.get('before',1)))
        if method=='symbolic':self.history['symbolic_ms_per_candidate']=metrics.get('wall_seconds',0)*1000/count
        elif 'feature_cpu_seconds' in metrics:
            self.history['feature_ms_per_candidate']=metrics['feature_cpu_seconds']*1000/count
            self.history['alpha_fixed_ms']=metrics.get('optimization_seconds',0)*1000
            if features:self.history['alpha_reference_work']=count*max(1,features['K_X'])*max(1,features['candidate_complexity'])
    def decide(self, features):
        n=features["library_size"];retained=features["prefilter_count"]
        sym=self.history.get("symbolic_ms_per_candidate",.003)*retained
        work=retained*max(1,features['K_X'])*max(1,features['candidate_complexity'])
        kernel=self.history.get('alpha_fixed_ms',80.)
        if 'alpha_reference_work' in self.history:kernel=max(40.,kernel*work/max(1,self.history['alpha_reference_work']))
        alpha=self.history.get("feature_ms_per_candidate",.002)*retained + kernel
        if self.mode in ("pure_symbolic","cpu_only"): chosen="symbolic"
        elif self.mode=="gpu_width": chosen="continuous" if features["gpu_available"] else "symbolic"
        elif self.mode=="hybrid": chosen="hybrid"
        elif n<512 or alpha>=sym or not features["gpu_available"]: chosen="symbolic"
        else: chosen="hybrid" if features["retention_ratio"]<.25 else "continuous"
        return {"chosen":chosen,"features":dict(features),"historical_timing":dict(self.history),"estimated_symbolic_ms":sym,"estimated_alpha_ms":alpha,
                "reason":"CPU-first measured/estimated cost policy; correctness gates remain mandatory"}
