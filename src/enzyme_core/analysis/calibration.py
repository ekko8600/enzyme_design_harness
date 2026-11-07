"""Standalone ranking and binary-classification metrics for fixed inputs."""
from __future__ import annotations
from math import sqrt
from typing import Iterable


def _ranks(values: list[float]) -> list[float]:
    ordered=sorted(enumerate(values),key=lambda item:item[1]); result=[0.0]*len(values); i=0
    while i<len(ordered):
        end=i+1
        while end<len(ordered) and ordered[end][1]==ordered[i][1]: end+=1
        rank=(i+1+end)/2
        for pos,_ in ordered[i:end]: result[pos]=rank
        i=end
    return result


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left)<2 or len(set(left))<2 or len(set(right))<2: return None
    a,b=_ranks(left),_ranks(right); am=sum(a)/len(a); bm=sum(b)/len(b)
    numerator=sum((x-am)*(y-bm) for x,y in zip(a,b))
    denominator=sqrt(sum((x-am)**2 for x in a)*sum((y-bm)**2 for y in b))
    return numerator/denominator if denominator else None


def evaluate_calibration(labels: Iterable[int|bool], scores: Iterable[int|float], *, top_k: int|None=None) -> dict:
    """Return separate ranking/classification metrics; scores stay uncombined."""
    raw=list(labels); values=[float(x) for x in scores]
    if not raw or len(raw)!=len(values) or any(x not in (0,1,False,True) for x in raw):
        raise ValueError("CALIBRATION_INPUT_INVALID")
    if any(not (float('-inf')<x<float('inf')) for x in values): raise ValueError("CALIBRATION_SCORE_NONFINITE")
    y=[int(x) for x in raw]; n=len(y); k=min(top_k or 5,n)
    if k<1: raise ValueError("CALIBRATION_TOP_K_INVALID")
    selected={i for i,_ in sorted(enumerate(values),key=lambda pair:(-pair[1],pair[0]))[:k]}
    positives=sum(y); negatives=n-positives; tp=sum(y[i] for i in selected)
    pos=[v for label,v in zip(y,values) if label]; neg=[v for label,v in zip(y,values) if not label]
    auroc=(sum(1 if p>q else .5 if p==q else 0 for p in pos for q in neg)/(len(pos)*len(neg))) if pos and neg else None
    ordered=sorted(zip(values,y), key=lambda pair: pair[0], reverse=True)
    found=0; ap=0.0; cursor=0
    while cursor<len(ordered):
        end=cursor+1
        while end<len(ordered) and ordered[end][0]==ordered[cursor][0]: end+=1
        group_positive=sum(label for _,label in ordered[cursor:end])
        found+=group_positive
        ap+=group_positive*found/end
        cursor=end
    precision=tp/k; recall=tp/positives if positives else None; prevalence=positives/n
    rejected=sum(not y[i] for i in range(n) if i not in selected)
    return {"n":n,"positive_count":positives,"negative_count":negatives,
            "spearman":_correlation([float(x) for x in y],values),"auroc":auroc,
            "auprc":ap/positives if positives else None,"top_k":k,
            "top_k_precision":precision,"top_k_recall":recall,
            "top_k_enrichment":precision/prevalence if prevalence else None,
            "negative_rejection_rate":rejected/negatives if negatives else None}
