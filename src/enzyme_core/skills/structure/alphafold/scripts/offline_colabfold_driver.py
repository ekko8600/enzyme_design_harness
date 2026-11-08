"""Run ColabFold with frozen local weights/MSA and forbid Internet connections."""
import sys
from pathlib import Path


def main():
    import socket
    original_connect=socket.socket.connect;original_connect_ex=socket.socket.connect_ex
    def connect(self,address):
        if self.family in (socket.AF_INET,socket.AF_INET6):raise RuntimeError('OFFLINE_COLABFOLD_NETWORK_FORBIDDEN')
        return original_connect(self,address)
    def connect_ex(self,address):
        if self.family in (socket.AF_INET,socket.AF_INET6):raise RuntimeError('OFFLINE_COLABFOLD_NETWORK_FORBIDDEN')
        return original_connect_ex(self,address)
    socket.socket.connect=connect;socket.socket.connect_ex=connect_ex
    import colabfold.batch as batch
    import os,json
    import jax
    backend=jax.default_backend()
    if os.environ.get('ENZYME_REQUIRE_GPU')=='1' and backend!='gpu':raise RuntimeError('COLABFOLD_GPU_REQUIRED_NO_CPU_FALLBACK')
    output=Path(sys.argv[-1]);output.mkdir(parents=True,exist_ok=True)
    (output/'runtime_device.json').write_text(json.dumps({'backend':backend,'devices':[str(d) for d in jax.devices()],'gpu_required':os.environ.get('ENZYME_REQUIRE_GPU')=='1'}))
    def frozen_weights_only(model_type,data_dir,*args,**kwargs):
        if model_type not in {'alphafold2_ptm','alphafold2','AlphaFold2-ptm'}:raise ValueError('OFFLINE_MODEL_TYPE_NOT_FROZEN')
        # The caller already validates every explicitly requested weight.
        if not Path(data_dir,'params','params_model_1_ptm.npz').is_file():raise ValueError('FROZEN_AF2_MODEL1_WEIGHT_MISSING')
    batch.download_alphafold_params=frozen_weights_only
    batch.main()


if __name__=='__main__':main()
