"""Analyze registered protein MD evidence; time comes from OpenMM steps, not DCD defaults."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''): h.update(b)
    return h.hexdigest()


def mass_weighted_rg(xyz, masses):
    import numpy as np
    weights = np.asarray(masses, dtype=float)
    if not np.isfinite(weights).all() or np.any(weights <= 0):
        raise ValueError('PROTEIN_ATOM_MASSES_INVALID')
    weights /= weights.sum()
    centers = np.einsum('fai,a->fi', xyz, weights)
    return np.sqrt(np.einsum('fai,fai,a->f', xyz - centers[:, None, :],
                            xyz - centers[:, None, :], weights))


def analyze(trajectory, topology, manifest, state, out_dir):
    import numpy as np
    import pandas as pd
    import mdtraj as md
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation, PillowWriter

    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    meta = json.loads(Path(manifest).read_text())
    if meta['schema_version'] != 'openmm-md-run/v2': raise ValueError('MD_MANIFEST_VERSION_UNSUPPORTED')
    for role, path in [('traj_dcd', trajectory), ('complex_pdb', topology), ('state_tsv', state)]:
        record = meta['artifacts'][role]
        if record['sha256'] != sha(path): raise ValueError('MD_INPUT_HASH_MISMATCH:' + role)
    traj = md.load(str(trajectory), top=str(topology))
    if traj.n_frames < 2 or not np.isfinite(traj.xyz).all(): raise ValueError('TRAJECTORY_INVALID')
    states = pd.read_csv(state, sep='\t')
    step_col = next(c for c in states if c.strip('#"') == 'Step')
    steps = states[step_col].to_numpy(dtype=float)
    dt = float(meta['derived']['step_size_ps']); eq = int(meta['args']['equilibration_steps'])
    production_ps = (steps - eq) * dt
    if len(steps) != traj.n_frames or not np.isfinite(production_ps).all() or np.any(np.diff(steps) <= 0):
        raise ValueError('TRAJECTORY_STATE_FRAME_OR_TIME_MISMATCH')
    duration = float(meta['derived']['trajectory_duration_ps'])
    if production_ps[0] <= 0 or abs(production_ps[-1] - duration) > dt:
        raise ValueError('PRODUCTION_DURATION_NOT_CONFIRMED')
    protein = traj.topology.select('protein'); backbone = traj.topology.select('protein and backbone')
    ca = traj.topology.select('protein and name CA')
    if not len(backbone) or not len(ca): raise ValueError('PROTEIN_SELECTION_EMPTY')
    # OpenMM writes this DCD with enforcePeriodicBox=False: connected molecules remain whole.
    protein_set = set(protein)
    bonds = np.array([(a.index, b.index) for a, b in traj.topology.bonds if a.index in protein_set and b.index in protein_set])
    disulfides = [{'residue_a': a.residue.resSeq, 'residue_b': b.residue.resSeq,
                   'chain_a': a.residue.chain.index, 'chain_b': b.residue.chain.index}
                  for a,b in traj.topology.bonds if a.name == b.name == 'SG' and a.element.symbol == b.element.symbol == 'S']
    if len(bonds):
        distances = md.compute_distances(traj, bonds, periodic=False)
        if float(distances.max()) > .3: raise ValueError('PROTEIN_BOND_DISCONTINUITY_OR_PBC_BREAK')
    rmsd = md.rmsd(traj, traj, frame=0, atom_indices=backbone, parallel=False) * 10
    protein_traj = traj.atom_slice(protein)
    masses = np.array([atom.element.mass for atom in protein_traj.topology.atoms])
    rg = mass_weighted_rg(protein_traj.xyz, masses) * 10
    ions = [a.index for a in traj.topology.atoms if a.element is not None and a.element.symbol == 'Ca']
    oxygen = traj.topology.select('protein and element O')
    table = pd.DataFrame({'time_ns': production_ps / 1000, 'rmsd_backbone_A': rmsd, 'radius_gyration_A': rg})
    for ion in ions:
        pairs = np.array([(ion, int(j)) for j in oxygen])
        table['calcium_' + str(ion) + '_nearest_protein_O_A'] = md.compute_distances(traj, pairs, periodic=True).min(axis=1) * 10
    temperature = next(c for c in states if 'Temperature' in c)
    energy = next(c for c in states if 'Potential Energy' in c)
    table['temperature_K'] = states[temperature].to_numpy()
    table['potential_energy_kJ_mol'] = states[energy].to_numpy()
    if not np.isfinite(table.to_numpy()).all(): raise ValueError('NONFINITE_ANALYSIS')
    table.to_csv(out / 'timeseries.csv', index=False)
    traj.superpose(traj, frame=0, atom_indices=backbone)
    xyz = traj.xyz[:, ca, :]
    rmsf = np.sqrt(np.mean(np.sum((xyz - xyz.mean(axis=0)) ** 2, axis=2), axis=0)) * 10
    residues = [traj.topology.atom(int(i)).residue for i in ca]
    pd.DataFrame({'chain_index': [r.chain.index for r in residues], 'residue_id': [r.resSeq for r in residues],
                  'residue_name': [r.name for r in residues], 'rmsf_CA_A': rmsf}).to_csv(out / 'rmsf.csv', index=False)
    fig, axes = plt.subplots(2, 2, figsize=(11, 7), layout='constrained')
    for ax, column, label in zip(axes.flat, ['rmsd_backbone_A','radius_gyration_A','temperature_K','potential_energy_kJ_mol'],
                                ['Backbone RMSD (Angstrom)', 'Protein Rg (Angstrom)', 'Temperature (K)', 'Potential energy (kJ/mol)']):
        ax.plot(table.time_ns, table[column], linewidth=1); ax.set(xlabel='Production time (ns)', ylabel=label)
    fig.suptitle('Protein MD: one short trajectory'); fig.savefig(out/'md_overview.png',dpi=160); plt.close(fig)
    fig, axes = plt.subplots(2,1,figsize=(10,7),layout='constrained')
    axes[0].plot([r.resSeq for r in residues],rmsf);axes[0].set(xlabel='Residue ID',ylabel='CA RMSF (Angstrom)')
    for col in table:
        if col.startswith('calcium_'): axes[1].plot(table.time_ns,table[col],label=col)
    axes[1].set(xlabel='Production time (ns)',ylabel='Nearest protein oxygen (Angstrom)')
    if ions: axes[1].legend(fontsize=8)
    fig.savefig(out/'flexibility_calcium.png',dpi=160);plt.close(fig)
    fig = plt.figure(figsize=(7,6)); ax = fig.add_subplot(projection='3d')
    first,last = xyz[0]*10,xyz[-1]*10
    ax.plot(*first.T,label='First saved frame');ax.plot(*last.T,label='Last saved frame');ax.legend()
    ax.set(xlabel='X (Angstrom)',ylabel='Y (Angstrom)',zlabel='Z (Angstrom)',title='Backbone-aligned CA trace')
    fig.savefig(out/'structure_overlay.png',dpi=160);plt.close(fig)
    indices = np.linspace(0,traj.n_frames-1,min(40,traj.n_frames),dtype=int)
    fig=plt.figure(figsize=(6,5));ax=fig.add_subplot(projection='3d');line,=ax.plot(*first.T)
    bounds=np.stack([xyz.min(axis=(0,1)),xyz.max(axis=(0,1))])*10
    ax.set(xlim=bounds[:,0],ylim=bounds[:,1],zlim=bounds[:,2],xlabel='X (Angstrom)',ylabel='Y (Angstrom)',zlabel='Z (Angstrom)')
    def update(index):
        points=xyz[index]*10;line.set_data_3d(*points.T);ax.set_title(f'CA trace, backbone aligned: {production_ps[index]/1000:.3f} ns');return (line,)
    FuncAnimation(fig,update,frames=indices,interval=150).save(out/'trajectory.gif',writer=PillowWriter(fps=7));plt.close(fig)
    summary={'schema_version':'enzyme-core/protein-md-analysis/v1','frames':traj.n_frames,'protein_residues':len(ca),
             'calcium_ions':len(ions),'disulfide_bonds':disulfides,'production_duration_ns':production_ps[-1]/1000,'first_saved_frame_ns':production_ps[0]/1000,
             'rmsd_reference':'first saved production frame; backbone fit','rmsf_reference':'CA deviations from mean after backbone alignment to first saved frame',
             'radius_gyration_definition':'Mass-weighted radius of gyration of all protein atoms; solvent and ions excluded.',
             'time_source':'OpenMM StateDataReporter Step minus manifest equilibration steps; DCD default time is ignored',
             'rmsd_backbone_A':{'min':float(rmsd.min()),'max':float(rmsd.max()),'last':float(rmsd[-1])},
             'temperature_K_mean':float(table.temperature_K.mean()),'inputs':{k:{'path':str(v),'sha256':sha(v)} for k,v in [('trajectory',trajectory),('topology',topology),('manifest',manifest),('state',state)]},
             'limitations':['Single short simulation; not convergence, replicated sampling, enzyme activity or experimental stability evidence.',
                            'Equilibration uses the existing implementation: the target-temperature thermostat and solvent barostat remain active throughout, with staged velocity reassignment. Stage labels do not establish a true thermostat ramp or separate NVT ensemble.',
                            'Nonbonded calcium model; no metal-site-specific validation. Original crystallographic water is replaced.',
                            'Plots/animation show actual sampled coordinates. Only CA trace is shown in structural views; water and ions are omitted from those views.',
                            'This operation supports fresh equilibrated runs, not restart time accounting.'], 'synthetic_data':False}
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary))
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trajectory',required=True);parser.add_argument('--topology',required=True)
    parser.add_argument('--manifest',required=True);parser.add_argument('--state',required=True);parser.add_argument('--out-dir',required=True)
    args=parser.parse_args()
    analyze(args.trajectory,args.topology,args.manifest,args.state,args.out_dir)


if __name__=='__main__':main()
