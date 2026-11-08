#!/usr/bin/env python3
"""visualize_ensembles.py — Visualize per-state MD conformational ensembles.

From the cluster-representative PDBs (cluster_ensemble.sh output, <state>_rep.cN.pdb) +
cluster populations (<state>_clusters.summary), produces a publication figure:
  * protein-Cα PCA conformational landscape (points = cluster centroids, size ∝ population, colored by state)
  * per-state active-site overlay renders (PyMOL, open-source — no watermark)
  * top-cluster population bars
Output: fig_ensembles.png in --outdir.

Usage:
  visualize_ensembles.py --ens-dir ensembles --states R,TS,P --nprot 559 \
     --active-site 'resi 560 or resi 181 or resi 406' [--pymol <open-source pymol>] [--outdir figures]
"""
import argparse, os, shutil, subprocess
import numpy as np

STCOL={'R':'#0072B2','NAC':'#56B4E9','TS':'#D55E00','P':'#009E73'}

def find_pymol(u):
    for c in [u, shutil.which('pymol')]:
        if c and os.path.exists(c): return c
    return 'pymol'

def render_overlays(ensdir, states, active, anchor, pymol, outdir, near_cut=9.0, zoom=7.0):
    """Per-state active-site conformational-cloud overlays with cartoon backbone context.
    Lessons baked in (glucoamylase worked example):
      * frame each panel on `anchor` = catalytic residues, PROTEIN-ONLY. PDB resSeq wraps
        past 9999, so in large solvated systems waters collide with low resids (e.g. 181/406);
        without `and polymer` those wrapped waters render as green scatter everywhere.
      * show sticks only for `active` atoms WITHIN near_cut Å of the anchor — in the product
        state the substrate is released/renamed and diffuses; this drops the diffused copies
        so the cloud stays coherent (and no explicit water is ever shown).
      * faded cartoon of nearby backbone gives spatial context; tight zoom + high ray res = sharp."""
    body=f"""
from pymol import cmd
cmd.bg_color('white'); cmd.set('ray_opaque_background',1); cmd.set('orthoscopic',1)
cmd.set('ray_shadows',0); cmd.set('stick_radius',0.14); cmd.set('antialias',2)
scol={{'R':'0x0072B2','NAC':'0x56B4E9','TS':'0xD55E00','P':'0x009E73'}}
import glob,os
for st in {states!r}:
    reps=sorted(glob.glob(f'{ensdir}/{{st}}_rep.c*.pdb'))
    if not reps: continue
    cmd.delete('all')
    for i,f in enumerate(reps): cmd.load(f,f'{{st}}_{{i}}')
    for i in range(1,len(reps)): cmd.align(f'{{st}}_{{i}} and name CA', f'{{st}}_0 and name CA')
    ref=f'{{st}}_0'
    cmd.select('anc','({anchor}) and polymer and not resn WAT+HOH')           # protein-only frame anchor
    cmd.select('near', f'byres ({{ref}} and polymer within 12 of ({{ref}} and anc))')
    cmd.hide('everything')
    cmd.show('cartoon','near'); cmd.color('gray80','near'); cmd.set('cartoon_transparency',0.55,ref)
    cmd.select('sticks_sel', '(({active}) and not resn WAT+HOH) within {near_cut} of anc')  # no water; drop diffused copies
    cmd.show('sticks','sticks_sel'); cmd.set('stick_transparency',0.25,'sticks_sel')
    cmd.color(scol.get(st,'0x888888'),'sticks_sel')
    cmd.orient(f'{{ref}} and anc'); cmd.zoom(f'{{ref}} and anc',{zoom})
    cmd.ray(1500,1300); cmd.png(f'{outdir}/ens_{{st}}.png',dpi=300)
"""
    open(f'{outdir}/_ens.pml','w').write("python\n"+body+"\npython end\n")
    subprocess.run([pymol,'-cq',f'{outdir}/_ens.pml'],check=False)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--ens-dir',default='ensembles'); ap.add_argument('--states',default='R,TS,P')
    ap.add_argument('--nprot',type=int,default=559,help='last protein residue (for Cα PCA)')
    ap.add_argument('--active-site',default='resi 560 or resi 181 or resi 406')
    ap.add_argument('--anchor',default=None,help="framing anchor = catalytic residues, e.g. 'resi 181 or resi 406' (defaults to --active-site); auto protein-only")
    ap.add_argument('--near-cut',type=float,default=9.0,help='show active-site sticks only within this many Å of anchor (drops wrapped waters / diffused product copies)')
    ap.add_argument('--zoom',type=float,default=7.0)
    ap.add_argument('--pymol',default=None); ap.add_argument('--outdir',default='figures')
    ap.add_argument('--skip-render',action='store_true')
    a=ap.parse_args(); os.makedirs(a.outdir,exist_ok=True)
    states=a.states.split(',')
    anchor=a.anchor or a.active_site
    if not a.skip_render: render_overlays(a.ens_dir,states,a.active_site,anchor,find_pymol(a.pymol),a.outdir,a.near_cut,a.zoom)
    import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt, matplotlib.image as mpimg
    plt.rcParams.update({'font.family':'DejaVu Sans','axes.spines.top':False,'axes.spines.right':False,'savefig.dpi':300,'savefig.bbox':'tight'})
    def ca(fn):
        return np.array([[float(l[30:38]),float(l[38:46]),float(l[46:54])] for l in open(fn,encoding='latin-1')
                         if l[:4]=='ATOM' and l[12:16].strip()=='CA' and int(l[22:26])<=a.nprot])
    def kab(P,Q):
        Pc,Qc=P-P.mean(0),Q-Q.mean(0); H=Pc.T@Qc; U,S,Vt=np.linalg.svd(H)
        d=np.sign(np.linalg.det(Vt.T@U.T)); return Pc@(Vt.T@np.diag([1,1,d])@U.T).T
    def pops(st):
        f=f'{a.ens_dir}/{st}_clusters.summary'; p={}
        if os.path.exists(f):
            for l in open(f):
                x=l.split()
                if x and x[0].isdigit(): p[int(x[0])]=float(x[2])
        return p
    import glob
    X=[];lab=[];sz=[];ref=None
    for st in states:
        reps=sorted(glob.glob(f'{a.ens_dir}/{st}_rep.c*.pdb')); pp=pops(st)
        for i,f in enumerate(reps):
            c=ca(f)
            if ref is None: ref=c-c.mean(0)
            X.append(kab(c,ref).flatten()); lab.append(st); sz.append(30+pp.get(i,0.01)*1200)
    X=np.array(X);lab=np.array(lab);sz=np.array(sz); Xc=X-X.mean(0); U,S,Vt=np.linalg.svd(Xc,full_matrices=False)
    pc=Xc@Vt[:2].T; var=(S**2/np.sum(S**2))[:2]*100
    def crop(im):
        msk=(im[...,:3]<0.985).any(2)
        if msk.any():
            ys,xs=np.where(msk); im=im[max(ys.min()-8,0):ys.max()+8,max(xs.min()-8,0):xs.max()+8]
        return im
    fig=plt.figure(figsize=(20,13.5))
    # top row: large per-state ensemble overlays (cartoon context, no water)
    for i,st in enumerate(states[:3]):
        p=f'{a.outdir}/ens_{st}.png'
        if not os.path.exists(p): continue
        axi=fig.add_axes([0.03+i*0.325,0.50,0.30,0.44]); axi.imshow(crop(mpimg.imread(p)))
        axi.set_title(f'{st} ensemble  ({len(sorted(glob.glob(f"{a.ens_dir}/{st}_rep.c*.pdb")))} clusters)',
                      color=STCOL.get(st,'#888'),fontsize=20,fontweight='bold',pad=6); axi.axis('off')
    # bottom-left: PCA landscape
    axp=fig.add_axes([0.06,0.07,0.40,0.36])
    for st in states:
        m=lab==st; axp.scatter(pc[m,0],pc[m,1],s=sz[m],color=STCOL.get(st,'#888'),alpha=.65,ec='white',lw=.9,label=st)
    axp.set_xlabel(f'PC1 ({var[0]:.0f}%)',fontsize=14);axp.set_ylabel(f'PC2 ({var[1]:.0f}%)',fontsize=14)
    axp.set_title('Protein Cα PCA landscape (size ∝ population)',fontsize=15)
    axp.legend(frameon=False,title='State',fontsize=13,title_fontsize=13); axp.tick_params(labelsize=12)
    # bottom-right: top cluster populations
    axb=fig.add_axes([0.56,0.07,0.40,0.36]); w=0.8/len(states)
    for j,st in enumerate(states):
        top=sorted(pops(st).values(),reverse=True)[:8]; axb.bar(np.arange(len(top))+j*w,[t*100 for t in top],w,color=STCOL.get(st,'#888'),label=st)
    axb.set_xlabel('cluster rank',fontsize=14);axb.set_ylabel('population %',fontsize=14)
    axb.set_title('Top cluster populations',fontsize=15);axb.legend(frameon=False,fontsize=13); axb.tick_params(labelsize=12)
    fig.suptitle('MD conformational ensembles per catalytic state',fontsize=21,fontweight='bold',y=0.985)
    fig.savefig(f'{a.outdir}/fig_ensembles.png',dpi=300); print(f'wrote {a.outdir}/fig_ensembles.png (PC1/2 = {var.round(1)})')

if __name__=='__main__': main()
