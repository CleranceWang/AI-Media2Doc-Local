r"""诊断：这个 .env 里到底有什么、缺什么。只打码显示，不回显真值。

配合 m2d.bat 的 [E] 看凭据来源 使用。

为什么要它：`.env` 的有效性经常被误判 ——
  - 值是占位符（CHANGE_ME），看起来"有值"，实际必填项没填
  - MSYS/git-bash 风格的空值陷阱：`VAR=` 和 `VAR=""` 在 compose 里行为不同
  - 键名拼错（WHISPER_MODEL 少个 _HOST），compose 里 `${VAR:?}` 才报错，
    而报错信息不告诉你是拼错了
  - .env 里的值带尾随空格，肉眼看不见，签名时才会炸

用法：
    python scripts\env_info.py [路径]
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

# 必填项 —— 判据是 compose 里用了 ${VAR:?}（缺了直接报错）。
# 从 config\docker-compose.yaml grep '${[A-Z_]*:?' 得到，不要凭印象加：
#   RUSTFS_ACCESS_KEY / RUSTFS_SECRET_KEY / WHISPER_MODEL_HOST
# 其余都是 ${VAR:-默认值}，可以不填。
REQUIRED = [
    "RUSTFS_ACCESS_KEY",
    "RUSTFS_SECRET_KEY",
    "WHISPER_MODEL_HOST",
]

# 有默认值、可以不填的（compose 里用 ${VAR:-...} 或容器内固定值）
OPTIONAL = [
    "STORAGE_BUCKET",
    "STORAGE_REGION",
    "LLM_BASE_URL",
    "MODEL_ID",
    "LLM_API_KEY",
    "RETENTION_DAYS",
    "CLEANUP_INTERVAL_SECONDS",
    "CLEANUP_INITIAL_DELAY_SECONDS",
    "WEB_ACCESS_PASSWORD",
]

# 项目曾经的变量，现已废弃。留着会让后来者困惑（尤其是指向旧目录的那些）。
DEPRECATED = {
    "M2D_SRC": "旧「补丁源目录」机制，改动已直接进源码树",
    "M2D_BUILD": "旧「补丁副本」机制，同上",
    "CLEANUP_RETENTION_DAYS": "改名为 RETENTION_DAYS",
    "STORAGE_ENDPOINT": "不再从 .env 读，由 compose 固定为宿主机视角的 http://127.0.0.1:9000",
}

PLACEHOLDER_HINTS = ("CHANGE_ME", "xxx", "your-", "sk-xxx")


def mask(v: str) -> str:
    if not v:
        return ""
    if len(v) <= 8:
        return "*" * len(v)
    return v[:4] + "*" * (len(v) - 8) + v[-4:]


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if not path or not path.exists():
        print(f"  找不到文件: {path}")
        return 1

    print(f"  路径: {path}")
    print(f"  大小: {path.stat().st_size} B")

    raw = path.read_bytes()
    print(f"  编码: {'UTF-8 BOM' if raw[:3] == b'\xef\xbb\xbf' else 'UTF-8 无 BOM'}"
          f"{'  ⚠️ 有 BOM，首行键名可能带不可见字符' if raw[:3] == b'\xef\xbb\xbf' else ''}")

    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    print(f"  换行: CRLF={crlf} LF={lf}")

    env: dict[str, str] = {}
    order: list[str] = []
    for line in io.open(path, encoding="utf-8-sig"):
        raw_line = line
        line = line.rstrip("\r\n")
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        # 引号包裹的算已剥壳；未剥壳的提醒
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        env[k] = v
        order.append(k)
        # 尾随空格检查
        if raw_line.rstrip("\r\n") != raw_line.rstrip():
            print(f"  ⚠️  {k} 行尾有空白字符")

    print(f"\n  --- 必填项 ---")
    missing, bad = [], []
    for k in REQUIRED:
        if k not in env:
            print(f"  [缺] {k}")
            missing.append(k)
        elif not env[k]:
            print(f"  [空] {k}")
            missing.append(k)
        elif any(h in env[k] for h in PLACEHOLDER_HINTS):
            print(f"  [占位] {k} = {env[k]}   ← 还是模板值")
            bad.append(k)
        else:
            print(f"  [OK] {k} = {mask(env[k])}")

    print(f"\n  --- 可选项 ---")
    for k in OPTIONAL:
        if k in env and env[k]:
            # 敏感项一律打码。判据是「键名里是否含这些词」，不是「值长什么样」——
            # 靠值判断会漏（sk- 开头的长度不定），靠白名单会随新增变量失效。
            sensitive = any(t in k.upper() for t in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
            shown = mask(env[k]) if sensitive else env[k]
            print(f"  [OK] {k} = {shown}")
        else:
            print(f"  [--] {k} 未设置（用默认值）")

    extra = [k for k in order if k not in REQUIRED and k not in OPTIONAL]

    deprecated = [k for k in order if k in DEPRECATED]
    if deprecated:
        print(f"\n  --- 已废弃的 {len(deprecated)} 项（建议删掉）---")
        for k in deprecated:
            print(f"  [废] {k}")
            print(f"       {DEPRECATED[k]}")

    stale = [k for k in extra if k not in DEPRECATED]
    if stale:
        print(f"\n  --- 其它 {len(stale)} 项 ---")
        for k in stale:
            print(f"       {k} = {mask(env[k]) if env[k] else '(空)'}")

    print()
    if missing or bad:
        print(f"  结论: 必填项有 {len(missing)} 个缺失、{len(bad)} 个仍是占位符")
        print("        compose 里用了 ${VAR:?} 强制必填，缺了会直接报错")
        return 1
    print("  结论: 必填项齐全")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
