r"""环境自检：跑之前先看这里，别等失败了才猜。

为什么要有这个：这个项目的失败模式全是"环境不对"，而且报错位置离原因很远 ——
  - Docker 没启动      -> docker compose 报管道错误，看起来像权限问题
  - GPU 不可用         -> ASR 静默降级到 CPU，转写慢 20 倍，你不会立刻发现
  - 模型没挂载         -> ASR 容器起来但第一次转写才炸
  - 桶没建 / CORS 没配 -> 浏览器上传报"网络错误"，后端日志却干干净净
  - API key 是占位符   -> 转写成功，AI 总结那一步才失败

所以一次把这几项全查了，给出「能跑 / 不能跑」的明确结论。

用法：
    python scripts\doctor.py
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = ROOT / "config" / ".env.example"

# 允许把 .env 放在仓库之外（默认仍在 config\ 下）。
# 为什么需要：有些人刻意让仓库里不存在真实凭据（这样即使 .gitignore 出错也泄不出去）。
# 探测顺序必须与 m2d.bat 一致，否则「bat 能跑但 doctor 说没凭据」，让人白排查。
#   ① --env-file 显式指定   ② 环境变量 M2D_ENV
#   ③ 仓库内 .env.local     ④ 从仓库根逐级向上找 .secrets\media2doc.env（最多 4 层）
#   ⑤ 仓库内 config\.env（兜底，给普通 clone 用户）
ENV_FILE = ROOT / "config" / ".env"
_env_override: str | None = None

for _i, _a in enumerate(sys.argv):
    if _a == "--env-file" and _i + 1 < len(sys.argv):
        _env_override = sys.argv[_i + 1]
    elif _a.startswith("--env-file="):
        _env_override = _a.split("=", 1)[1]

if not _env_override and os.environ.get("M2D_ENV"):
    _env_override = os.environ["M2D_ENV"]

if not _env_override and (ROOT / ".env.local").exists():
    _env_override = str(ROOT / ".env.local")

if not _env_override:
    _p = ROOT
    for _ in range(4):
        _cand = _p / ".secrets" / "media2doc.env"
        if _cand.exists():
            _env_override = str(_cand)
            break
        _p = _p.parent

if _env_override:
    ENV_FILE = Path(_env_override).expanduser().resolve()

OK = "[ OK ]"
WARN = "[WARN]"
FAIL = "[FAIL]"

_failed = 0
_warned = 0


def say(tag: str, msg: str) -> None:
    global _failed, _warned
    if tag == FAIL:
        _failed += 1
    elif tag == WARN:
        _warned += 1
    print(f"  {tag} {msg}")


def run(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, "command not found"
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def http_code(url: str, timeout: int = 6) -> int:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def load_env() -> dict[str, str]:
    if not ENV_FILE.exists():
        return {}
    out: dict[str, str] = {}
    for line in io.open(ENV_FILE, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def check_docker() -> None:
    print("\n[1/8] Docker")
    rc, out = run(["docker", "version", "--format", "{{.Server.Version}}"])
    if rc != 0:
        say(FAIL, "Docker 守护进程不可用 —— 先把 Docker Desktop 打开")
        say(WARN, f"      原始输出: {out.strip()[:120]}")
        return
    say(OK, f"Docker 引擎 {out.strip()}")

    rc, out = run(["docker", "compose", "version"])
    if rc != 0:
        say(FAIL, "docker compose 插件缺失")
        return
    m = re.search(r"v?([\d.]+)", out)
    say(OK, f"compose {m.group(1) if m else out.strip()[:40]}")


def check_gpu() -> None:
    print("\n[2/8] GPU")
    nvidia = shutil.which("nvidia-smi")
    if not nvidia:
        say(WARN, "宿主机找不到 nvidia-smi")
        say(WARN, "      若 NVIDIA 驱动已装但没进 PATH，可忽略；ASR 在容器内用 GPU")
        return
    rc, out = run([nvidia, "--query-gpu=name,memory.total,driver_version",
                   "--format=csv,noheader"])
    if rc != 0:
        say(WARN, "nvidia-smi 执行失败")
        return
    for line in out.strip().splitlines():
        say(OK, f"GPU: {line.strip()}")

    rc, out = run(["docker", "run", "--rm", "--gpus", "all",
                   "nvidia/cuda:12.4.1-base-ubuntu22.04", "nvidia-smi", "-L"],
                  timeout=180)
    if rc != 0:
        say(FAIL, "GPU 无法透进容器 —— ASR 会退回 CPU，转写会慢很多")
        say(WARN, "      先确认 Docker Desktop 用的是 WSL2 后端且驱动已装")
        return
    say(OK, "GPU 可透进容器")


def check_env_file() -> dict[str, str]:
    print("\n[3/8] 配置文件")
    if not ENV_FILE.exists():
        say(FAIL, f"缺少 {ENV_FILE}")
        if ENV_EXAMPLE.exists():
            say(WARN, "      生成: Copy-Item config\\.env.example config\\.env")
            say(WARN, "      或指定外部文件: python scripts\\doctor.py --env-file <路径>")
        return {}
    env = load_env()
    note = "  ← 外部文件" if _env_override else ""
    say(OK, f"{ENV_FILE.name} 存在（{len(env)} 项）{note}")
    return env


def check_keys(env: dict[str, str]) -> None:
    print("\n[4/8] 凭据")
    placeholders = {"", "CHANGE_ME", "sk-xxx"}

    key = env.get("LLM_API_KEY", "")
    if not key or key in placeholders or "CHANGE_ME" in key:
        say(FAIL, "LLM_API_KEY 还是占位符 —— 跑 m2d.bat key 填上")
    elif not key.startswith("sk-"):
        say(WARN, f"LLM_API_KEY 不以 sk- 开头（长度 {len(key)}），确认一下")
    else:
        say(OK, f"LLM_API_KEY 已填（{key[:6]}...{key[-4:]}，{len(key)} 字符）")

    for name in ("RUSTFS_ACCESS_KEY", "RUSTFS_SECRET_KEY"):
        v = env.get(name, "")
        if not v or "CHANGE_ME" in v:
            say(FAIL, f"{name} 未设置")
        elif "/" in v:
            say(FAIL, f"{name} 含斜杠 —— SigV4 签名会解析错，换掉")
        else:
            say(OK, f"{name} 已设置（{len(v)} 字符）")


def check_compose() -> None:
    print("\n[5/8] Compose 配置")
    compose = ROOT / "config" / "docker-compose.yaml"
    if not compose.exists():
        say(FAIL, f"缺少 {compose}")
        return
    rc, out = run(["docker", "compose", "--project-directory", str(ROOT / "config"),
                   "--env-file", str(ENV_FILE), "config", "--services"])
    if rc != 0:
        say(FAIL, "compose 配置解析失败")
        say(WARN, f"      {out.strip()[:200]}")
        return
    svcs = [s for s in out.split() if s]
    expected = {"rustfs", "backend", "asr", "frontend"}
    missing = expected - set(svcs)
    if missing:
        say(FAIL, f"缺少服务: {', '.join(sorted(missing))}")
    else:
        say(OK, f"四个服务齐全: {', '.join(sorted(svcs))}")


def check_model() -> None:
    print("\n[6/8] Whisper 模型")
    env = load_env()
    host_path = env.get("WHISPER_MODEL_HOST", "")
    if not host_path:
        say(FAIL, "WHISPER_MODEL_HOST 未设置")
        return
    p = Path(host_path)
    if not p.exists():
        say(FAIL, f"模型目录不存在: {host_path}")
        return
    bin_file = p / "model.bin"
    if not bin_file.exists():
        say(FAIL, f"缺 model.bin（faster-whisper 需要它）: {host_path}")
        return
    size_mb = bin_file.stat().st_size / 1024 / 1024
    if size_mb < 100:
        say(WARN, f"model.bin 只有 {size_mb:.1f} MB，large-v3 应该约 2944 MB")
    else:
        say(OK, f"model.bin {size_mb:.1f} MB")


def check_containers() -> None:
    print("\n[7/8] 运行状态")
    rc, out = run(["docker", "ps", "-a", "--filter", "name=m2d-",
                   "--format", "{{.Names}}\t{{.Status}}"])
    if rc != 0 or not out.strip():
        say(WARN, "没有 m2d-* 容器，还没启动过（正常，跑 m2d.bat up）")
        return
    rows = [r for r in out.strip().splitlines() if r.strip()]
    for r in rows:
        parts = r.split("\t")
        name, status = parts[0], parts[1] if len(parts) > 1 else "?"
        if "Up" in status:
            say(OK, f"{name}: {status}")
        else:
            say(WARN, f"{name}: {status}（未运行）")

    print("\n      端点连通性:")
    for label, url, good in (
        ("前端", "http://127.0.0.1:5173", {200}),
        ("后端", "http://127.0.0.1:8080/docs", {200}),
        ("ASR ", "http://127.0.0.1:8001/health", {200}),
        ("S3  ", "http://127.0.0.1:9000", {403}),
    ):
        code = http_code(url)
        if code in good:
            note = "（403 = 无凭据访问，属正常）" if label == "S3  " else ""
            say(OK, f"{label} HTTP {code} {note}")
        elif code == 0:
            say(WARN, f"{label} 连不上（容器没跑或端口没映射）")
        else:
            say(WARN, f"{label} HTTP {code}，预期 {sorted(good)}")

    hc = http_code("http://127.0.0.1:8001/health")
    if hc == 200:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8001/health", timeout=6) as r:
                d = json.loads(r.read().decode("utf-8"))
            dev = d.get("device")
            if dev == "cuda":
                say(OK, "ASR 使用 CUDA")
            elif dev:
                say(WARN, f"ASR 设备是 {dev} —— 不是 cuda，速度会差很多")
            if d.get("model_loaded"):
                say(OK, "模型已加载")
            else:
                say(OK, "模型未加载（懒加载，首次转写时才载，正常）")
        except Exception as e:
            say(WARN, f"解析 /health 失败: {e}")


def check_bucket() -> None:
    print("\n[8/8] 桶与 CORS")
    env = load_env()
    if not env:
        say(WARN, "跳过（没有 .env）")
        return
    code = http_code("http://127.0.0.1:9000")
    if code == 0:
        say(WARN, "RustFS 未运行，跳过（跑 m2d.bat up 后再试）")
        return

    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import boto3  # type: ignore
        from botocore.config import Config  # type: ignore
    except ImportError:
        say(WARN, "宿主机没装 boto3，无法检查桶")
        say(WARN, "      用容器版检查: docker compose ... run --rm asr python3 -c '...'")
        return

    endpoint = env.get("STORAGE_ENDPOINT", "http://127.0.0.1:9000")
    bucket = env.get("STORAGE_BUCKET", "media2doc")
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=env.get("RUSTFS_ACCESS_KEY"),
            aws_secret_access_key=env.get("RUSTFS_SECRET_KEY"),
            region_name=env.get("STORAGE_REGION", "cn-east-1"),
            verify=False,
            config=Config(signature_version="s3v4",
                          s3={"addressing_style": "path"},
                          retries={"max_attempts": 2}),
        )
        s3.head_bucket(Bucket=bucket)
        say(OK, f"桶 {bucket} 存在")
    except Exception as e:
        msg = str(e)
        if "NoSuchBucket" in msg or "404" in msg:
            say(FAIL, f"桶 {bucket} 不存在 —— 跑 python scripts\\init_rustfs.py")
        elif "InvalidAccessKeyId" in msg or "SignatureDoesNotMatch" in msg:
            say(FAIL, "凭据不对 —— 检查 .env 里的 RUSTFS_ACCESS_KEY / SECRET")
        else:
            say(FAIL, f"访问桶失败: {msg[:150]}")
        return

    try:
        cors = s3.get_bucket_cors(Bucket=bucket)
        rules = cors.get("CORSRules", [])
        if not rules:
            say(FAIL, "桶没有 CORS 规则 —— 浏览器直传会被拦（表现为『网络错误』）")
        else:
            origins = [o for r in rules for o in r.get("AllowedOrigins", [])]
            say(OK, f"CORS 已配置（允许来源: {', '.join(origins) or '?'}）")
    except Exception as e:
        msg = str(e)
        if "NoSuchCORSConfiguration" in msg or "404" in msg:
            say(FAIL, "桶没有 CORS 配置 —— 跑 python scripts\\init_rustfs.py")
            say(WARN, "      症状: 浏览器上传报『网络错误』，后端日志却无异常")
        else:
            say(WARN, f"读 CORS 失败: {msg[:120]}")


def main() -> int:
    print("=" * 58)
    print("  AI-Media2Doc-Local  环境自检")
    print("=" * 58)

    check_docker()
    check_gpu()
    env = check_env_file()
    check_keys(env)
    check_compose()
    check_model()
    check_containers()
    check_bucket()

    print("\n" + "=" * 58)
    if _failed:
        print(f"  结论: {_failed} 项失败、{_warned} 项警告 —— 先修失败项")
    elif _warned:
        print(f"  结论: 通过，{_warned} 项警告（多半不影响使用）")
    else:
        print("  结论: 全部通过")
    print("=" * 58)
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
