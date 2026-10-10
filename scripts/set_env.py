r"""安全地改 env 文件里的某一项。

给 m2d.bat 的「填 API Key」用。为什么要单独一个脚本，而不是让 bat 自己改：
  - bat 处理特殊字符（key 里可能有 `&`、`|`、`%`）会炸；Python 不会
  - 必须以 UTF-8 无 BOM + LF 写回，bat 做不到
  - 只在「键已存在」时替换，避免拼错键名却静默新增一行

用法：
    python scripts\set_env.py <KEY> <VALUE> [ENV_FILE]

ENV_FILE 可省略。省略时按与 m2d.bat / doctor.py **完全相同**的顺序探测：
    1. 环境变量 M2D_ENV 指定的文件
    2. 仓库内 .env.local
    3. 从仓库根逐级向上最多 4 层，找 .secrets\media2doc.env
    4. 兜底 config\.env

三个脚本必须用同一套顺序，否则会出现「bat 说找到了、set_env 说没这一项」。
"""

from __future__ import annotations

import io
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_UP_LEVELS = 4
SECRETS_REL = Path(".secrets") / "media2doc.env"


def find_env_file() -> tuple[Path | None, str]:
    """返回 (路径, 说明)。找不到返回 (None, 说明)。"""
    forced = os.environ.get("M2D_ENV")
    if forced:
        p = Path(forced)
        return (p, "环境变量 M2D_ENV") if p.is_file() else (None, f"环境变量 M2D_ENV 指向的文件不存在: {p}")

    local = ROOT / ".env.local"
    if local.is_file():
        return local, "仓库内 .env.local"

    p = ROOT
    for level in range(MAX_UP_LEVELS + 1):
        cand = p / SECRETS_REL
        if cand.is_file():
            return cand, f"向上 {level} 层找到 .secrets"
        if p.parent == p:
            break
        p = p.parent

    fallback = ROOT / "config" / ".env"
    if fallback.is_file():
        return fallback, "兜底 config\\.env"

    return None, "以上位置都没有"


def set_env(key: str, value: str, env_file: Path | None = None, source: str = "") -> int:
    if env_file is None:
        env_file, source = find_env_file()

    if env_file is None:
        print(f"  [失败] 找不到任何 env 文件（{source}）")
        print("         找过这些位置：")
        print("           - 环境变量 M2D_ENV")
        print(f"           - {ROOT / '.env.local'}")
        print(f"           - 从 {ROOT} 逐级向上 {MAX_UP_LEVELS} 层的 {SECRETS_REL}")
        print(f"           - {ROOT / 'config' / '.env'}")
        print("         先跑 m2d.bat setup")
        return 1

    if not env_file.is_file():
        print(f"  [失败] 找不到 {env_file}")
        print("         先跑 m2d.bat setup")
        return 1

    # 键名只允许 A-Z0-9_，防止正则注入
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        print(f"  [失败] 非法键名: {key!r}")
        return 1

    text = io.open(env_file, encoding="utf-8").read()
    pattern = re.compile(rf"(?m)^{re.escape(key)}=.*$")

    if not pattern.search(text):
        print(f"  [失败] {env_file} 里没有 {key} 这一项")
        print("         不新建，避免拼错键名却看起来成功")
        print(f"         （已定位的文件来自：{source}）")
        return 1

    # 用函数式替换，避免 value 里的 \1 \g<0> 等被当成反向引用
    new_text = pattern.sub(lambda _m: f"{key}={value}", text, count=1)

    # UTF-8 无 BOM + LF，与本仓库约定一致
    with io.open(env_file, "w", encoding="utf-8", newline="\n") as f:
        f.write(new_text)

    masked = value if len(value) <= 8 else value[:4] + "*" * (len(value) - 8) + value[-4:]
    print(f"  [OK] {key} = {masked}")
    print(f"       写入 {env_file}")
    return 0


def main() -> int:
    if len(sys.argv) not in (3, 4):
        print(__doc__)
        return 2
    if len(sys.argv) == 4:
        p = Path(sys.argv[3])
        return set_env(sys.argv[1], sys.argv[2], p, "命令行指定")
    return set_env(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    raise SystemExit(main())
