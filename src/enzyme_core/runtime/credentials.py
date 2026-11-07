"""Explicit environment grants and streaming secret redaction for generic calls."""
import os
import re
import threading

BASE={'ENZYME_HTTP_AUDIT','ENZYME_HTTP_MAX_BYTES','PATH','LANG','LC_ALL','LC_CTYPE','LD_LIBRARY_PATH','CONDA_PREFIX','CONDA_DEFAULT_ENV','SSL_CERT_FILE','SSL_CERT_DIR','HTTPS_PROXY','HTTP_PROXY','NO_PROXY','CUDA_VISIBLE_DEVICES','OMP_NUM_THREADS','MKL_NUM_THREADS','PYTHONPATH'}

def environment(names, overrides, workdir):
    env={k:v for k,v in os.environ.items() if k in BASE or k in names}
    if any(k in {'BASH_ENV','ENV','PYTHONSTARTUP','PYTHONHOME','LD_PRELOAD','CODEX_HOME'} or k not in names and re.search(r'PASSWORD|TOKEN|SECRET|API_KEY',k) for k in overrides):raise ValueError('UNDECLARED_OPERATION_ENVIRONMENT')
    env.update(overrides);env['HOME']=str(workdir)
    return env,[env[n].encode() for n in names if env.get(n)]


def pump(source,destination,secrets):
    values=sorted(set(secrets),key=len,reverse=True)
    pattern=re.compile(b'|'.join(re.escape(v) for v in values)) if values else None
    keep=max(map(len,values),default=1)-1;pending=b''
    try:
        while True:
            chunk=source.read(65536)
            pending+=chunk
            limit=len(pending) if not chunk else max(0,len(pending)-keep)
            pos=0
            if pattern:
                while pos<limit:
                    match=pattern.search(pending,pos)
                    if not match or match.start()>=limit:break
                    destination.write(pending[pos:match.start()]);destination.write(b'[REDACTED]');pos=match.end()
            destination.write(pending[pos:limit] if pos<limit else b'');pending=pending[max(pos,limit):]
            if not chunk:break
    finally:source.close();destination.flush()


def readers(process,out,err,secrets):
    threads=[threading.Thread(target=pump,args=(source,dest,secrets),daemon=True) for source,dest in [(process.stdout,out),(process.stderr,err)]]
    for t in threads:t.start()
    return threads
