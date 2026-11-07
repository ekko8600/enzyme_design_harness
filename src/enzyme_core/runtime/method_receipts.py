"""Deterministic method execution checks, separate from scientific result quality."""
import hashlib,json
from pathlib import Path


def receipt_spec(operation,argv,artifacts,work_dir=None):
    specs=operation.get('runtime_contract',{}).get('method_receipts',{})
    value=specs.get(argv[0]) if argv else None
    if not value:return None
    if value['output_flag'] not in argv:raise ValueError('METHOD_OUTPUT_BINDING_MISSING')
    pos=argv.index(value['output_flag'])
    if pos+1>=len(argv):raise ValueError('METHOD_OUTPUT_BINDING_MISSING')
    directory=argv[pos+1]
    def bound_path(path):
        return (Path(work_dir)/path).resolve() if work_dir is not None else Path(path)
    if not any(bound_path(a['path'])==bound_path(directory) and a['format']=='collection' for a in artifacts):raise ValueError('METHOD_RECEIPT_REQUIRES_COLLECTION')
    return {'path':str(Path(directory)/value['filename']),'accepted_statuses':value['accepted_statuses'],'diagnostic_statuses':value.get('diagnostic_statuses',[])}


def verify(root,spec):
    if not spec:return {'status':'UNVERIFIED_LEGACY','accepted':None}
    root=Path(root).resolve();raw=root/spec['path'];path=raw.resolve()
    if root not in path.parents or raw.is_symlink() or not path.is_file():
        return {'status':'METHOD_RECEIPT_MISSING','accepted':False}
    try:
        content=path.read_bytes();value=json.loads(content)
        if not isinstance(value,dict):raise ValueError('receipt must be an object')
    except (OSError,ValueError,UnicodeError) as exc:
        return {'status':'METHOD_RECEIPT_INVALID','accepted':False,'reason':str(exc)}
    status=value.get('method_status');returncode=value.get('returncode')
    accepted=status in spec['accepted_statuses']
    if accepted and (type(returncode) is not int or returncode!=0):
        return {'status':'METHOD_RECEIPT_INCONSISTENT','accepted':False,
                'reported_status':status,'backend_returncode':returncode}
    return {'status':status,'accepted':accepted,
            'diagnostic_only':status in spec.get('diagnostic_statuses',[]),'path':str(path),
            'sha256':hashlib.sha256(content).hexdigest(),'backend_returncode':returncode}
