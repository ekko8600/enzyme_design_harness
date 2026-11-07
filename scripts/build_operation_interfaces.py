"""Build Core published interfaces using its packaged static compiler."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from enzyme_core.runtime.interface_builder import build,extract
if __name__=='__main__':
    from collections import Counter
    result=build();print(len(result),dict(Counter(x['interface_status'] for x in result.values())))
