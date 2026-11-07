"""Resolve explicit existing model assets without invoking a model download helper."""
from pathlib import Path
from .config import load_config


def model_file(resource,name):
    if Path(name).name!=name:raise ValueError('MODEL_NAME_INVALID')
    value=load_config().get('model_weights',{}).get('models',{}).get(resource,{})
    root=Path(value.get('path',''))
    path=root/(name+'.pt')
    if not path.is_file():raise ValueError('OFFLINE_MODEL_FILE_MISSING:'+str(path))
    return path
