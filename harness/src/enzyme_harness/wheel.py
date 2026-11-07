"""Build a pure-Python wheel from a validated package without executing build hooks."""
from pathlib import Path
import base64
import csv
import hashlib
import io
import zipfile


def package_wheel(source,name,version,destination,requirements=()):
    source=Path(source);destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    path=destination/f'{name}-{version}-py3-none-any.whl';dist=f'{name}-{version}.dist-info';records=[]
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as archive:
        def add(name,data):
            archive.writestr(name,data);h=base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode();records.append([name,'sha256='+h,len(data)])
        for file in sorted(source.rglob('*')):
            if file.is_file() and '__pycache__' not in file.parts and file.suffix not in {'.pyc','.pyo'}:
                add(name+'/'+file.relative_to(source).as_posix(),file.read_bytes())
        metadata=f'Metadata-Version: 2.1\nName: {name.replace("_","-")}\nVersion: {version}\n'
        for requirement in requirements:metadata+='Requires-Dist: '+requirement+'\n'
        add(dist+'/METADATA',(metadata+'\n').encode())
        add(dist+'/WHEEL',b'Wheel-Version: 1.0\nGenerator: enzyme-harness\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        if name=='enzyme_harness':add(dist+'/entry_points.txt',b'[console_scripts]\nenzyme-harness = enzyme_harness.launcher:main\n')
        output=io.StringIO();writer=csv.writer(output,lineterminator='\n');writer.writerows(records+[[dist+'/RECORD','','']]);archive.writestr(dist+'/RECORD',output.getvalue())
    return path
