"""Record the one agent process before exec, and never run it without its controller."""
from pathlib import Path
import os
import sys
import time
from .files import alive,identity,load,sha,write


def main(folder):
    folder=Path(folder);spec=load(folder/'bootstrap.json');mine=identity()
    write(folder/'process.json',{'identity':mine,'agent':spec.get('agent'),'role':spec['role'],'version':spec['version'],
          'auth_directory':spec['auth_directory'],'context_sha256':spec['context_sha256'],'bootstrap_id':spec['id']})
    while True:
        approval=load(folder/'launch.allow')
        if approval:
            if approval.get('bootstrap_sha256')!=sha(folder/'bootstrap.json') or approval.get('identity')!=mine:
                raise RuntimeError('CODEX_BOOTSTRAP_APPROVAL_MISMATCH')
            try:os.execvpe(spec['command'][0],spec['command'],os.environ)
            except OSError as exc:
                write(folder/'bootstrap-exec-failed.json',{'bootstrap_id':spec['id'],'identity':mine,'error':str(exc)})
                return 126
        if not alive(spec['controller']):
            write(folder/'bootstrap-aborted.json',{'bootstrap_id':spec['id'],'reason':'CONTROLLER_EXITED_BEFORE_LAUNCH'})
            return 0
        time.sleep(.05)


if __name__=='__main__':raise SystemExit(main(sys.argv[1]))
