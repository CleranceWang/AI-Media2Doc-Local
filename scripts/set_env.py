r"""安全地改 config\.env 里的某一项。

给 m2d.bat 的「填 API Key」用。为什么要单独一个脚本，而不是让 bat 自己改：
  - bat 处理特殊字符（key 里可能有 `&`、`|`、`%`）会炸；Python 不会
  - 必须以 UTF-8 无 BOM + LF 写回，bat 做不到
  - 只在「键已存在」时替换，避免拼错键名却静默新增一行

用法：
    python scripts\set_env.py <KEY> <VALUE>
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / "config" / ".env"


def set_env(key: str, value: str) -> int:
    if not ENV_FILE.exists():
        print(f"  [失败] 找不到 {ENV_FILE}")
        print("         先跑 m2d.bat setup")
        return 1

    # 键名只允许 A-Z0-9_，防止正则注入
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        print(f"  [失败] 非法键名: {key!r}")
        return 1

    text = io.open(ENV_FILE, encoding="utf-8").read()
    pattern = re.compile(rf"(?m)^{re.escape(key)}=.*$")

    if not pattern.search(text):
        print(f"  [失败] {ENV_FILE.name} 里没有 {key} 这一项")
        print("         不新建，避免拼错键名却看起来成功")
        return 1

    # 用函数式替换，避免 value 里的 \1 \g<0> 等被当成反向引用
    new_text = pattern.sub(lambda _m: f"{key}={value}", text, count=1)

    # UTF-8 无 BOM + LF，与本仓库约定一致
    with io.open(ENV_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(new_text)

    masked = value if len(value) <= 8 else value[:4] + "*" * (len(value) - 8) + value[-4:]
    print(f"  [OK] {key} = {masked}")
    return 0


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    return set_env(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    raise SystemExit(main())
