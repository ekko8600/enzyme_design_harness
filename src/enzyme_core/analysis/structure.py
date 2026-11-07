"""Small pure-geometry functions for structure feature postprocessing."""
from __future__ import annotations
import math
from collections.abc import Sequence
Vector=tuple[float,float,float]

def distance(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a)!=3 or len(b)!=3: raise ValueError("COORDINATE_MUST_BE_3D")
    return math.sqrt(sum((float(x)-float(y))**2 for x,y in zip(a,b)))

def angle_degrees(a: Sequence[float], center: Sequence[float], b: Sequence[float]) -> float:
    if any(len(v)!=3 for v in (a,center,b)): raise ValueError("COORDINATE_MUST_BE_3D")
    u=[float(x)-float(y) for x,y in zip(a,center)]; v=[float(x)-float(y) for x,y in zip(b,center)]
    nu=math.sqrt(sum(x*x for x in u)); nv=math.sqrt(sum(x*x for x in v))
    if not nu or not nv: raise ValueError("ZERO_LENGTH_BOND")
    cosine=max(-1.0,min(1.0,sum(x*y for x,y in zip(u,v))/(nu*nv)))
    return math.degrees(math.acos(cosine))

def summarize(values: Sequence[float]) -> dict:
    data=[float(v) for v in values]
    if not data or any(not math.isfinite(v) for v in data): raise ValueError("FINITE_VALUES_REQUIRED")
    ordered=sorted(data); n=len(data); mean=sum(data)/n
    return {"count":n,"mean":mean,"sample_sd":math.sqrt(sum((v-mean)**2 for v in data)/(n-1)) if n>1 else None,
            "median":ordered[n//2] if n%2 else (ordered[n//2-1]+ordered[n//2])/2}

def compare_matched_replicates(reference: dict[str, float], observations: dict[str, dict[str, float]]) -> dict:
    """Compare explicit replicate-matched scalar observations by variant.

    Keys are replicate identifiers and must describe the same control protocol.
    The function reports numerical deltas only; it does not label mechanism or
    biological effect.
    """
    if not reference: raise ValueError("REFERENCE_REPLICATES_REQUIRED")
    ref={str(k):float(v) for k,v in reference.items()}
    if any(not math.isfinite(v) for v in ref.values()): raise ValueError("FINITE_VALUES_REQUIRED")
    output={}
    for name, raw in sorted(observations.items()):
        obs={str(k):float(v) for k,v in raw.items()}
        shared=sorted(set(ref)&set(obs))
        if not shared: output[name]={"matched_count":0,"delta_mean":None,"delta_sample_sd":None}; continue
        deltas=[obs[k]-ref[k] for k in shared]
        stats=summarize(deltas)
        output[name]={"matched_count":len(shared),"replicate_ids":shared,
                      "delta_mean":stats["mean"],"delta_sample_sd":stats["sample_sd"]}
    return {"reference_replicates":len(ref),"variants":output}
