# -*- coding: UTF-8 -*-
r"""初始化 RustFS：建桶 + 配置 CORS。

═══════════════════════════════════════════════════════════════════════════════
为什么必须有这个脚本
═══════════════════════════════════════════════════════════════════════════════
**浏览器直传 S3 那一跳需要 CORS，而 RustFS 没有任何 CLI/环境变量可以配它。**

现象：前端 PUT 上传报「网络错误」，但后端日志显示 presigned URL 签发成功、
RustFS 也收不到请求 —— 因为浏览器在做预检（OPTIONS）时没拿到
`Access-Control-Allow-Origin`，直接掐断了连接。

命令行工具（curl / Invoke-WebRequest / boto3）**不做 CORS 检查**，
所以用它们测「上传能不能用」会得到假的通过 —— 这个坑已实测踩过。

解法：用标准 S3 API `put_bucket_cors` 给桶配 CORS 规则。
这是 bucket 级配置，**存在 RustFS 的数据卷里**；只要数据卷还在就不会丢，
但**换新卷 / 清库后必须重跑本脚本**。

用法：
    python scripts\init_rustfs.py
    （从 config\.env 读端点和凭据）
═══════════════════════════════════════════════════════════════════════════════
"""
import sys
from pathlib import Path

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

# 允许的来源。前端跑在 :5173，但用户也可能用别的主机名访问，
# 本地自用环境用 * 最省事；若要收紧，改成 ['http://localhost:5173']。
ALLOWED_ORIGINS = ["*"]


def load_env(env_path: Path) -> dict:
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
    env_file = Path(__file__).resolve().parent.parent / "config" / ".env"
    cfg = load_env(env_file)

    endpoint = "http://127.0.0.1:9000"          # 宿主机视角
    ak = cfg.get("RUSTFS_ACCESS_KEY", "")
    sk = cfg.get("RUSTFS_SECRET_KEY", "")
    bucket = cfg.get("STORAGE_BUCKET", "media2doc")
    region = cfg.get("STORAGE_REGION", "cn-east-1")

    if not ak or ak.startswith("CHANGE_ME"):
        print("config\\.env 里没有有效的 RUSTFS_ACCESS_KEY，先填好。")
        return 2

    print("目标: %s  桶: %s  region: %s" % (endpoint, bucket, region))
    print()

    c = boto3.client(
        "s3",
        aws_access_key_id=ak,
        aws_secret_access_key=sk,
        endpoint_url=endpoint,
        region_name=region,
        verify=False,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )

    # ── 1) 建桶（幂等）────────────────────────────────────────────────────
    try:
        c.head_bucket(Bucket=bucket)
        print("[1/2] 桶 %s 已存在" % bucket)
    except ClientError:
        try:
            c.create_bucket(Bucket=bucket)
            print("[1/2] 已创建桶 %s" % bucket)
        except Exception as e:
            print("[1/2] 建桶失败: %s: %s" % (type(e).__name__, e))
            return 1

    # ── 2) 配 CORS ───────────────────────────────────────────────────────
    # ★ 这一步是浏览器上传能否工作的关键。缺了它前端会报「网络错误」。
    rules = [{
        "AllowedOrigins": ALLOWED_ORIGINS,
        "AllowedMethods": ["GET", "PUT", "POST", "DELETE", "HEAD"],
        "AllowedHeaders": ["*"],
        "ExposeHeaders": ["ETag"],
        "MaxAgeSeconds": 3600,
    }]
    try:
        c.put_bucket_cors(Bucket=bucket, CORSConfiguration={"CORSRules": rules})
        got = c.get_bucket_cors(Bucket=bucket)
        print("[2/2] CORS 已配置并读回验证:")
        for r in got["CORSRules"]:
            print("        origins=%s" % r.get("AllowedOrigins"))
            print("        methods=%s" % r.get("AllowedMethods"))
            print("        headers=%s" % r.get("AllowedHeaders"))
    except Exception as e:
        print("[2/2] 配 CORS 失败: %s: %s" % (type(e).__name__, e))
        return 1

    print()
    print("完成。浏览器直传所需的 CORS 已就绪。")
    print("提示：换新数据卷 / 清库后需重跑本脚本。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
