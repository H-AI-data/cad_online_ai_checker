from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from common.pipeline import main
if __name__=='__main__':
    raise SystemExit(main(fixed_module='2',fixed_dimension='1'))

