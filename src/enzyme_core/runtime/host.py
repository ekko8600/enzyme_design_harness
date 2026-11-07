"""Hash-bound Python Host delivery, always isolated and without network/auth."""
import ast
import hashlib
from pathlib import Path
from .isolation import command


def validate(prepared):
    program=prepared['host_program'];root=Path(prepared['work_dir']).resolve();path=Path(program['path'])
    if prepared['execution_kind']!='host_authored' or path.is_symlink() or root not in path.resolve().parents or not path.is_file():raise ValueError('HOST_PROGRAM_PATH_INVALID')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=program['sha256']:raise ValueError('HOST_PROGRAM_CHANGED')
    ast.parse(path.read_text())
    for spec in prepared['artifacts']:
        rel=Path(spec['path'])
        if not rel.parts or rel.parts[0]!='outputs' or len(rel.parts)<2:raise ValueError('HOST_OUTPUT_MUST_USE_OUTPUTS_DIRECTORY')
    return program


def launch(prepared):
    p=validate(prepared);root=Path(prepared['work_dir']).resolve()
    output=root/'outputs';cache=root/'cache'
    output.mkdir(exist_ok=True);cache.mkdir(exist_ok=True)
    for d in [output,cache]:
        if d.is_symlink() or root not in d.resolve().parents:raise ValueError('HOST_WRITE_ROOT_INVALID')
    guard = ("import os,runpy,sys,traceback\n"
             "try:\n runpy.run_path(sys.argv[1],run_name='__main__')\n"
             "except SystemExit as exc:\n"
             " code=exc.code if isinstance(exc.code,int) else (0 if exc.code is None else 1)\n"
             " if code: traceback.print_exc();sys.stdout.flush();sys.stderr.flush();os._exit(code if 0<code<256 else 1)\n"
             "except BaseException:\n traceback.print_exc();sys.stdout.flush();sys.stderr.flush();os._exit(1)\n")
    return command([p['python'],'-I','-c',guard,p['path']],python=p['python'],reads=[root]+p['read_paths'],writes=[output,cache],network=False)
