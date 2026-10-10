# -*- coding: UTF-8 -*-
r"""验证 LLM 端点可用（DeepSeek / 火山方舟 / OpenAI / Ollama 通用）。

═══════════════════════════════════════════════════════════════════════════════
为什么需要它
═══════════════════════════════════════════════════════════════════════════════
LLM 配错时，media2doc 的失败会出现在「生成 Markdown」那一步 —— 那时视频已经
传完、ASR 也跑完了，排查成本高。这个脚本让你在**动手之前**先确认三件事：
    1. 地址通不通（base_url 是否写对，特别是要不要带 /v1）
    2. key 有没有效
    3. 模型名认不认（MODEL_ID 这个名字来自火山方舟，其他家要换）

用法：
    python scripts\check_llm.py
    （从 config\.env 读 LLM_BASE_URL / MODEL_ID / LLM_API_KEY）

    # 或临时指定
    python scripts\check_llm.py --base https://api.deepseek.com/v1 --model deepseek-chat
═══════════════════════════════════════════════════════════════════════════════
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def load_env(env_path: Path) -> dict:
    """极简 .env 解析（不引入 python-dotenv 依赖）。"""
    out = {}
    if not env_path.exists():
        return out
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="验证 LLM 端点")
    ap.add_argument("--env", default=None, help=".env 路径，默认 config\\.env")
    ap.add_argument("--base", default=None, help="覆盖 LLM_BASE_URL")
    ap.add_argument("--model", default=None, help="覆盖 MODEL_ID")
    ap.add_argument("--key", default=None, help="覆盖 LLM_API_KEY（不建议，会进命令历史）")
    args = ap.parse_args()

    env_file = Path(args.env) if args.env else \
        Path(__file__).resolve().parent.parent / "config" / ".env"
    cfg = load_env(env_file)

    base = args.base or cfg.get("LLM_BASE_URL", "")
    model = args.model or cfg.get("MODEL_ID", "")
    key = args.key or cfg.get("LLM_API_KEY", "")

    print("配置文件：%s" % env_file)
    print("  base_url = %s" % (base or "(空)"))
    print("  model    = %s" % (model or "(空)"))
    print("  api_key  = %s" % (("已设置，%d 字符" % len(key)) if key and not key.startswith("CHANGE_ME")
                               else "★ 仍是占位符，未填真实 key"))
    print()

    problems = []
    if not base:
        problems.append("LLM_BASE_URL 为空")
    if not model:
        problems.append("MODEL_ID 为空")
    if not key or key.startswith("CHANGE_ME"):
        problems.append("LLM_API_KEY 未填真实值")

    # 常见坑：DeepSeek/OpenAI 需要 /v1 后缀，火山方舟需要 /api/v3
    if base and "deepseek" in base and not base.rstrip("/").endswith("/v1"):
        problems.append("DeepSeek 的 base_url 应以 /v1 结尾（当前：%s）" % base)

    if problems:
        print("发现问题，先修：")
        for p in problems:
            print("  • %s" % p)
        if any("未填真实值" in p for p in problems):
            print("\n填法（config\\.env）：")
            print("  LLM_BASE_URL=https://api.deepseek.com/v1")
            print("  MODEL_ID=deepseek-chat")
            print("  LLM_API_KEY=sk-你的key")
        return 2

    url = base.rstrip("/") + "/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5,
        "stream": False,
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + key)

    print("POST %s" % url)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode("utf-8"))
        reply = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = body.get("usage", {})
        print("\n[OK] LLM 可用")
        print("  回复: %r" % reply)
        print("  用量: prompt=%s completion=%s" % (usage.get("prompt_tokens"), usage.get("completion_tokens")))
        return 0
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        print("\n[FAIL] HTTP %d %s" % (e.code, e.reason))
        print("  %s" % detail)
        if e.code == 401:
            print("\n  => key 无效或已过期")
        elif e.code == 404:
            print("\n  => base_url 或 model 名不对。检查是否漏了 /v1，")
            print("     以及 MODEL_ID 是否是该服务商认识的模型名。")
        elif e.code == 429:
            print("\n  => 限流或余额不足")
        return 1
    except Exception as e:
        print("\n[FAIL] %s: %s" % (type(e).__name__, e))
        print("  => 网络不通，或 base_url 写错")
        return 1


if __name__ == "__main__":
    sys.exit(main())
