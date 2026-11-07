"""Immutable runtime identities and validated, atomic release activation."""
from pathlib import Path
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import time
from .files import digest, inside, load, sha, write


def _harness_files(root):
    """Every runtime module, including the agents subpackage, keyed by relative path."""
    return {p.relative_to(root).as_posix():sha(p) for p in sorted(root.rglob('*.py'))
            if '__pycache__' not in p.parts and 'acceptance' not in p.relative_to(root).parts}


def runtime_identity():
    import enzyme_core
    from . import __version__
    root=Path(__file__).parent
    core=Path(enzyme_core.__file__).parent
    return {'harness_version':__version__,'harness_sha256':digest(_harness_files(root)),
            'acceptance_sha256':digest({p.relative_to(root/'acceptance').as_posix():sha(p) for p in sorted((root/'acceptance').rglob('*.py'))}),
            'core_runtime_sha256':digest({p.relative_to(core).as_posix():sha(p) for p in sorted(core.rglob('*.py')) if not {'skills','third_party','__pycache__'}.intersection(p.relative_to(core).parts)}),
            'core_interfaces_sha256':sha(core/'runtime/operation_interfaces.json'),
            'core_api_sha256':sha(core/'api.py'),'python':str(Path(sys.executable).absolute())}


def candidate_sources(task,failure):
    import enzyme_core
    root=task.path/'repairs'/failure['id']/('candidate-'+str(task.state()['repairs']))
    root.mkdir(parents=True,exist_ok=True)
    copy_ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.egg-info')
    shutil.copytree(Path(__file__).parent,root/'harness',ignore=copy_ignore)
    shutil.copytree(Path(enzyme_core.__file__).parent,root/'core',ignore=copy_ignore)
    protected=sha(root/'harness/launcher.py')
    baseline={p.relative_to(root).as_posix():sha(p) for folder in ('harness','core') for p in (root/folder).rglob('*') if p.is_file()}
    write(root/'baseline.json',baseline)
    authority={'failure_id':failure['id'],'protected_launcher':protected,'baseline':baseline,'created_at':time.time()}
    write(root/'candidate.json',authority)
    write(root.parent/(root.name+'-authority.json'),authority)
    return root


def changed_files(root):
    root=Path(root);baseline=load(root.parent/(root.name+'-authority.json'))['baseline']
    current={p.relative_to(root).as_posix():sha(p) for folder in ('harness','core') for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
    changes=sorted(p for p in set(current)|set(baseline) if current.get(p)!=baseline.get(p))
    if (root/'environment.json').is_file():changes.append('environment.json')
    return sorted(changes)


def synchronize_interfaces(root):
    """Trusted static rebuild; never execute candidate code or merely bless new hashes."""
    root=Path(root)
    if not any(p.startswith('core/') for p in changed_files(root)):return []
    from enzyme_core.runtime.interface_builder import build
    build(root/'core')
    path='core/runtime/operation_interfaces.json'
    write(root.parent/(root.name+'-interfaces.json'),{'generated':{path:sha(root/path)},'compiler_sha256':sha(Path(__import__('enzyme_core.runtime.interface_builder',fromlist=['build']).__file__))})
    return [path] if path in changed_files(root) else []


def validate_candidate(task, root):
    """Run trusted native regressions and an actual baseline/candidate reproduction."""
    root=Path(root); authority=load(root.parent/(root.name+'-authority.json'))
    if not authority or sha(root/'harness/launcher.py')!=authority['protected_launcher']:
        raise ValueError('LAUNCHER_IS_PROTECTED')
    generated=synchronize_interfaces(root); changes=changed_files(root)
    if not changes:raise ValueError('CANDIDATE_HAS_NO_CHANGES')
    reproduction=load(root/'reproduction.json',{})
    argv=reproduction.get('argv');expected=reproduction.get('expected_error','')
    failure=load(root.parent/'failure.json',{})
    if not isinstance(argv,list) or not argv or not all(isinstance(x,str) for x in argv):raise ValueError('REPRODUCTION_COMMAND_REQUIRED')
    if len(expected)<8 or expected not in str(failure.get('error','')):raise ValueError('REPRODUCTION_MUST_MATCH_ORIGINAL_FAILURE')
    if (root/'environment.json').is_file():
        from .environment import propose
        spec=load(root/'environment.json');wheels=[x.get('path') if isinstance(x,dict) else x for x in spec.get('wheels',[])]
        propose(task,root,spec['python'],wheels)
    modules=root/'modules';modules.mkdir(exist_ok=True)
    for name,source in [('enzyme_harness','harness'),('enzyme_core','core')]:
        target=modules/name
        if not target.exists():target.symlink_to(root/source,target_is_directory=True)
    from .sandbox import wrap
    tests=Path(__file__).parent/'acceptance'
    baseenv={k:v for k,v in os.environ.items() if not any(w in k.upper() for w in ('TOKEN','PASSWORD','SECRET','API_KEY','CODEX_HOME'))}
    baseenv['PYTHONDONTWRITEBYTECODE']='1'
    original=os.pathsep.join(str(Path(p).absolute()) for p in sys.path if p)
    def execute(command,pythonpath):
        env=dict(baseenv);env['PYTHONPATH']=pythonpath
        return subprocess.run(wrap(command,writes=[root],reads=[tests,task.path],network=False),cwd=root,env=env,capture_output=True,text=True)
    baseline=execute(argv,original);candidate=execute(argv,str(modules))
    baseline_text=baseline.stdout+baseline.stderr
    if baseline.returncode==0 or expected not in baseline_text:raise ValueError('ORIGINAL_FAILURE_NOT_REPRODUCED')
    if candidate.returncode!=0:raise ValueError('CANDIDATE_REPRODUCTION_FAILED:'+candidate.stderr[-1000:])
    suite=execute([sys.executable,'-m','unittest','discover','-s',str(tests),'-p','test_*.py','-v'],str(modules))
    (root/'validation.log').write_text(suite.stdout+suite.stderr)
    if suite.returncode:raise ValueError('CANDIDATE_REGRESSION_FAILED:'+suite.stderr[-2000:])
    files={p.relative_to(root).as_posix():sha(p) for folder in ('harness','core') for p in sorted((root/folder).rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
    proof={'status':'PASS','changes':changes,'candidate_sha256':digest({p:sha(root/p) if (root/p).exists() else None for p in changes}),
           'package_files':files,'generated_files':generated,'reproduction':{'baseline_returncode':baseline.returncode,
           'candidate_returncode':candidate.returncode,'expected_error':expected,'baseline_stdout':baseline.stdout,
           'baseline_stderr':baseline.stderr,'candidate_stdout':candidate.stdout,'candidate_stderr':candidate.stderr},
           'regression_returncode':suite.returncode,'trusted_suite_sha256':sha(tests),'time':time.time()}
    write(root/'validation.json',proof);write(root.parent/(root.name+'-validation.json'),proof)
    return proof

def build_release(task,root, *, activate=True):
    """Build installable candidate wheels without editing Conda or shared packages."""
    root=Path(root);proof=load(root.parent/(root.name+'-validation.json'))
    if not proof or proof['status']!='PASS':raise ValueError('CANDIDATE_NOT_VALIDATED')
    changes=changed_files(root)
    package_files={p.relative_to(root).as_posix():sha(p) for folder in ('harness','core') for p in sorted((root/folder).rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
    if package_files!=proof['package_files']:raise ValueError('CANDIDATE_PACKAGE_CHANGED_AFTER_VALIDATION')
    current=digest({p:sha(root/p) if (root/p).exists() else None for p in changes})
    if changes!=proof['changes'] or current!=proof['candidate_sha256']:raise ValueError('CANDIDATE_CHANGED_AFTER_VALIDATION')
    rid=digest({'baseline':load(root.parent/(root.name+'-authority.json'))['baseline'],'validation':proof})[:24]
    release=task.root/'releases'/rid
    if release.exists():raise ValueError('RELEASE_ALREADY_EXISTS')
    release.mkdir(parents=True)
    wheels=release/'wheels';wheels.mkdir()
    # Wheel assembly is standard ZIP metadata, no candidate build hooks execute.
    from .wheel import package_wheel
    from . import __version__
    hw=package_wheel(root/'harness','enzyme_harness',__version__,wheels,requirements=['enzyme-core>=0.1.22','jsonschema>=4.18','psutil>=5.9'])
    cw=package_wheel(root/'core','enzyme_core',importlib.metadata.version('enzyme-core'),wheels,requirements=['PyYAML>=6'])
    envdir=release/'environment'
    from .environment import create
    python=create(root,envdir)
    subprocess.run([str(python),'-m','pip','install','--no-index','--no-deps','--ignore-installed',str(cw),str(hw)],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={k:v for k,v in os.environ.items() if k not in {'PYTHONPATH','PYTHONHOME'}})
    installed_env={k:v for k,v in os.environ.items() if k not in {'PYTHONPATH','PYTHONHOME','CODEX_HOME'} and not any(s in k.upper() for s in ('TOKEN','PASSWORD','SECRET','API_KEY'))}
    installed_env['PYTHONNOUSERSITE']='1'
    from .sandbox import wrap
    from enzyme_core.runtime.isolation import python_runtime_roots
    verification=subprocess.run(wrap([str(python),'-m','unittest','discover','-s',str(Path(__file__).parent/'acceptance'),'-p','test_*.py'],reads=[release,*python_runtime_roots(str(python))],network=False),capture_output=True,text=True,cwd=release,env=installed_env)
    write(release/'installation.json',{'returncode':verification.returncode,'stdout':verification.stdout,'stderr':verification.stderr,'wheels':{p.name:sha(p) for p in wheels.glob('*.whl')}})
    if verification.returncode:raise ValueError('RELEASE_INSTALLED_ACCEPTANCE_FAILED:'+verification.stdout[-2000:]+verification.stderr[-5000:])
    import zipfile,hashlib
    for wheel,prefix,package in [(hw,'harness','enzyme_harness'),(cw,'core','enzyme_core')]:
        with zipfile.ZipFile(wheel) as archive:
            actual={prefix+'/'+n.split('/',1)[1]:hashlib.sha256(archive.read(n)).hexdigest() for n in archive.namelist() if n.startswith(package+'/')}
        expected={p:h for p,h in proof['package_files'].items() if p.startswith(prefix+'/')}
        if actual!=expected:raise ValueError('WHEEL_CONTENT_DIFFERS_FROM_VALIDATED_CANDIDATE')
    site=next((envdir/'lib').glob('python*/site-packages'));h=site/'enzyme_harness';c=site/'enzyme_core'
    installed_identity={'harness_version':__version__,'python':str(python),
        'harness_sha256':digest(_harness_files(h)),
        'acceptance_sha256':digest({p.relative_to(h/'acceptance').as_posix():sha(p) for p in sorted((h/'acceptance').rglob('*.py'))}),
        'core_runtime_sha256':digest({p.relative_to(c).as_posix():sha(p) for p in sorted(c.rglob('*.py')) if not {'skills','third_party','__pycache__'}.intersection(p.relative_to(c).parts)}),
        'core_interfaces_sha256':sha(c/'runtime/operation_interfaces.json'),'core_api_sha256':sha(c/'api.py')}
    environment_spec=load(root/'environment.json')
    environment_binding={'base_python':environment_spec['python'],'python':str(python)} if environment_spec else None
    value={'owner_task_id':task.id,'environment_binding':environment_binding,'runtime_identity':installed_identity,'schema_version':'enzyme-harness/release/v1','id':rid,'python':str(python),'validated':True,'candidate':str(root),'baseline_release':task.state()['release'],'wheels':{p.name:sha(p) for p in wheels.glob('*.whl')}}
    write(release/'release.json',value)
    maximum=task.request()['budget']['max_storage_bytes']
    if maximum and task.storage_bytes()>maximum:raise ValueError('STORAGE_BUDGET_EXHAUSTED')
    # Activation is authoritative only after both source and installed checks.
    if environment_binding and activate:
        config=load(task.root/'config.json')
        if config:
            write(task.root/'config-history'/('before-'+rid+'.json'),config)
            if config.get('python')==environment_binding['base_python']:config['python']=str(python)
            config['environments']={name:str(python) if path==environment_binding['base_python'] else path for name,path in config.get('environments',{}).items()}
            write(task.root/'config.json',config)
    if activate:write(task.root/'active-release.json',{**value,'manifest_sha256':sha(release/'release.json')})
    return value
