import sys
from pathlib import Path

local_deps=Path(__file__).resolve().parent.parent/".deps"
if local_deps.is_dir():
    sys.path.insert(0,str(local_deps))

from .ui import main
raise SystemExit(main())
