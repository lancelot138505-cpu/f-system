"""Trusted references and randomized input generation; imported only by verifier code."""
from __future__ import annotations
import random

def reference(task_kind,nums):
    if task_kind=='strict_run':
        if not nums:return 0
        best=run=1
        for i in range(1,len(nums)):
            run=run+1 if nums[i]>nums[i-1] else 1
            best=max(best,run)
        return best
    if task_kind=='nondecreasing_run':
        if not nums:return 0
        best=run=1
        for i in range(1,len(nums)):
            run=run+1 if nums[i]>=nums[i-1] else 1
            best=max(best,run)
        return best
    if task_kind=='sign_changes':
        return sum(nums[i]!=nums[i-1] for i in range(1,len(nums)))
    raise KeyError(task_kind)

def randomized_inputs(count,seed):
    rng=random.Random(seed);out=[]
    for i in range(count):
        kind=i%8
        n=rng.randrange(0,96)
        if kind==0:a=[]
        elif kind==1:a=[rng.randrange(-20,21)]
        elif kind==2:
            start=rng.randrange(-10000,10000);a=[start+j for j in range(n)]
        elif kind==3:
            start=rng.randrange(-10000,10000);a=[start-j for j in range(n)]
        elif kind==4:
            v=rng.randrange(-25,26);a=[v]*n
        elif kind==5:a=[rng.randrange(-5,6) for _ in range(n)]
        elif kind==6:a=[rng.randrange(-10000,0) for _ in range(n)]
        else:a=[rng.randrange(-100000,100001) for _ in range(n)]
        out.append(a)
    return out
