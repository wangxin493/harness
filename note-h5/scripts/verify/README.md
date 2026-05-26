# 验证脚本目录

此目录包含端到端功能验证脚本。

## 命名规范

- `test_<feature>.py` - 功能验证脚本
- `verify_<module>.py` - 模块验证脚本
- `e2e_<scenario>.py` - 端到端场景验证

## 使用方式

脚本会自动被 `scripts/validate.py` 调用。

## 示例

```python
#!/usr/bin/env python3
"""
测试某个功能
"""

import sys
from pathlib import Path

def main():
    # 实现验证逻辑
    print("验证中...")
    # 返回 0 表示成功，非 0 表示失败
    return 0

if __name__ == "__main__":
    sys.exit(main())
```
