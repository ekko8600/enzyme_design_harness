"""Candidate environment overlays. Existing interpreters and weights are immutable."""
from pathlib import Path
import re
import importlib.util
import importlib.metadata
import shutil
import subprocess
import sys
from .files import load,sha,write


def propose(task,candidate,python,wheels):
    allowed={task.request()['python'],*task.request().get('environments',{}).values()}
    if python not in allowed:raise ValueError('ENVIRONMENT_BASE_NOT_CONFIGURED')
    version=subprocess.run([python,'-c','import sys,json;print(json.dumps(list(sys.version_info[:2])))'],capture_output=True,text=True,check=True)
    import json
    if json.loads(version.stdout)!=list(sys.version_info[:2]):raise ValueError('ENVIRONMENT_PYTHON_ABI_REQUIRES_MATCHING_LOCAL_HARNESS_DEPENDENCIES')
    roots=[task.path,Path(task.request().get('wheelhouse',task.path/'wheelhouse'))]
    records=[]
    for value in wheels:
        path=Path(value).resolve()
        if path.suffix!='.whl' or not path.is_file() or not any(root==path or root in path.parents for root in roots):raise ValueError('LOCAL_WHEEL_REQUIRED')
        records.append({'path':str(path),'sha256':sha(path)})
    specification={'python':python,'wheels':records,'network':False}
    write(Path(candidate)/'environment.json',specification)
    return specification


def create(candidate,destination):
    specification=load(Path(candidate)/'environment.json',{'python':sys.executable,'wheels':[]})
    subprocess.run([specification['python'],'-m','venv','--system-site-packages',str(destination)],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    python=Path(destination)/'bin/python'
    # Copy already-installed dependency modules into the new prefix. This is
    # an offline overlay; no Conda prefix or shared distribution is edited.
    site=Path(destination)/'lib'/f'python{sys.version_info.major}.{sys.version_info.minor}'/'site-packages'
    site.mkdir(parents=True,exist_ok=True)
    dependencies={}
    for name in ['jsonschema','jsonschema_specifications','referencing','rpds','attrs','attr','yaml','psutil']:
        spec=importlib.util.find_spec(name)
        if not spec or not spec.origin:raise ValueError('BUILD_DEPENDENCY_UNAVAILABLE:'+name)
        source=Path(spec.origin).parent;target=site/name
        if source.resolve()!=target.resolve():shutil.copytree(source,target,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        dependencies[name]={'source':str(source),'sha256':sha(target)}
    for distribution in ['jsonschema','jsonschema-specifications','referencing','rpds-py','attrs','PyYAML','psutil']:
        metadata=importlib.metadata.distribution(distribution)
        path=Path(metadata._path)
        if path.is_dir():shutil.copytree(path,site/path.name,dirs_exist_ok=True)
    write(Path(destination)/'offline-dependencies.json',dependencies)
    for wheel in specification['wheels']:
        if sha(wheel['path'])!=wheel['sha256']:raise ValueError('LOCAL_WHEEL_CHANGED')
    if specification['wheels']:
        subprocess.run([str(python),'-m','pip','install','--no-index','--no-deps','--ignore-installed',*[w['path'] for w in specification['wheels']]],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    return python


def bind_request(task,base,python):
    request=task.request();previous=dict(request)
    if request['python']==base:request['python']=python
    request['environments']={name:python if value==base else value for name,value in request.get('environments',{}).items()}
    write(task.path/'requests'/('environment-'+str(task.state()['revision'])+'.json'),previous)
    write(task.path/'request.json',request)
