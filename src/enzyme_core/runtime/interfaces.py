"""Packaged invocation metadata. Reading it does not import scientific code."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from enzyme_core.skills.registry import project_root, describe, discover

@lru_cache(maxsize=1)
def directory():
    return json.loads((project_root()/'runtime/operation_interfaces.json').read_text())['operations']

def interface(skill_id, operation='default', *, verify=True):
    try:profile=describe(skill_id)['execution_profile']
    except KeyError as exc:raise ValueError('OPERATION_INTERFACE_UNKNOWN:'+skill_id) from exc
    operation=profile['default_operation'] if operation=='default' else operation
    key=skill_id+':'+operation
    if key not in directory(): raise ValueError('OPERATION_INTERFACE_MISSING:'+key)
    value=json.loads(json.dumps(directory()[key]))
    if verify:
        root=project_root().resolve()
        for rel,expected in value['source_identity'].items():
            path=root/rel
            if path.is_symlink() or root not in path.resolve().parents or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
                raise ValueError('OPERATION_INTERFACE_SOURCE_CHANGED:'+rel)
    return value

def capabilities(query='', category=None):
    rows=[]
    for skill in discover(query):
        if category is not None and skill['category']!=category:continue
        profile=describe(skill['id'])['execution_profile']
        for op,value in profile['operations'].items():
            key=skill['id']+':'+op;v=directory().get(key,{})
            rows.append({'operation':key,'skill_id':skill['id'],'category':skill['category'],'description':skill['description'],'kind':value['kind'],
                'inputs':sorted({a['name'] for a in v.get('arguments',[]) if a['path_kind'] in {'input','resource','scalar_or_input'}}),'parameters':sorted({a['name'] for a in v.get('arguments',[]) if a['path_kind']=='scalar'}),'outputs':sorted({a['name'] for a in v.get('arguments',[]) if a['path_kind']=='output'}) or (['declared_artifacts'] if value['kind']=='host_authored' else ['stdout_or_documented_native_outputs']),'effects':v.get('effects',profile.get('effects',{})),
                'runtime_contract':v.get('runtime_contract',{'status':'UNVERIFIED'}),'interface_status':v.get('interface_status','INTERFACE_MISSING'),'policy':v.get('policy','UNKNOWN'),
                'requires_modules':v.get('requires_modules',value.get('requires_modules',[])),'requires_resources':v.get('requires_resources',[]),
                'command_policies':v.get('command_policies',{}),'calling_limits':v.get('calling_limits',[]),'credentials':v.get('credentials',[]),'claim_ceiling':profile['evidence_boundary'],'runtime_status':'NOT_CHECKED','scientific_validation':'NOT_REVALIDATED'})
    return sorted(rows,key=lambda r:r['operation'])
