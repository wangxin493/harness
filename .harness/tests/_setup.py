"""共享测试工具：在 sys.path 中插入 .harness/，方便 `from lib.xxx import ...`。"""

import sys
from pathlib import Path

_HARNESS_DIR = Path(__file__).resolve().parent.parent
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))
