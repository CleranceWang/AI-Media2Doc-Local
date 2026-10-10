# -*- coding: UTF-8 -*-
r"""验证 RustFS 能否满足 media2doc 的 S3 需求。

═══════════════════════════════════════════════════════════════════════════════
为什么必须实测
═══════════════════════════════════════════════════════════════════════════════
OpenList 就是栽在这一步：签名可用（Q1 PASS），但寻址风格对不上。
RustFS 是**另一个实现**，不能因为「都是 S3 兼容」就假定行为一致。

media2doc 的实际需求（来自源码勘查）：
    backend\utils\s3.py:25    Config(s3={"addressing_style": "virtual"})
    backend\utils\s3.py:11    signature_version="s3v4"
    backend\utils\s3.py:33    generate_presigned_url("put_object")   ← 浏览器直传
    backend\utils\s3.py:43    generate_presigned_url("get_object")   ← 火山取对象

要验证的四个问题：
    Q1 签名能不能通过（AK/SK 有效）
    Q2 virtual-hosted 寻址能不能解析（bucket.127.0.0.1 这种域名）
    Q3 path 寻址下的 presigned PUT 能不能真的传上去
    Q4 path 寻址下的 presigned GET 能不能真的取回来

用法（凭据只从环境变量读，绝不落盘、绝不回显）：
    $env:S3_AK="..."; $env:S3_SK="..."
    python verify_rustfs_s3.py
═══════════════════════════════════════════════════════════════════════════════
"""
import os
import sys
import json
import urllib.request
import urllib.error

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

ENDPOINT = os.getenv("S3_ENDPOINT", "http://127.0.0.1:9000")
AK = os.getenv("S3_AK", "")
SK = os.getenv("S3_SK", "")
REGION = os.getenv("S3_REGION", "us-east-1")
BUCKET = os.getenv("S3_BUCKET", "media2doc")

if not AK or not SK:
    print("缺少凭据：需要环境变量 S3_AK / S3_SK")
    sys.exit(2)

RESULTS = []


def record(qid, name, ok, detail):
    RESULTS.append({"q": qid, "name": name, "ok": ok, "detail": detail})
    mark = "PASS" if ok else "FAIL"
    print("[%s] %s — %s" % (mark, qid, name))
    print("        %s" % detail)


def make_client(style):
    return boto3.client(
        "s3",
        aws_access_key_id=AK,
        aws_secret_access_key=SK,
        endpoint_url=ENDPOINT,
        region_name=REGION,
        verify=False,
        config=Config(signature_version="s3v4", s3={"addressing_style": style}),
    )


print("=" * 72)
print("RustFS S3 兼容性验证  endpoint=%s  bucket=%s" % (ENDPOINT, BUCKET))
print("=" * 72)

# ── Q0：连通性与签名 ────────────────────────────────────────────────────────
try:
    c = make_client("path")
    c.list_buckets()
    record("Q0", "签名通过（AK/SK 有效）", True, "list_buckets 成功")
except ClientError as e:
    code = e.response.get("Error", {}).get("Code", "?")
    record("Q0", "签名通过（AK/SK 有效）", False, "ClientError %s: %s" % (code, e))
    print("\n凭据或签名无效，后续测试无意义，终止。")
    sys.exit(1)
except Exception as e:
    record("Q0", "签名通过（AK/SK 有效）", False, "%s: %s" % (type(e).__name__, e))
    sys.exit(1)

# ── 建桶（幂等）─────────────────────────────────────────────────────────────
try:
    c.head_bucket(Bucket=BUCKET)
    print("桶 %s 已存在" % BUCKET)
except ClientError:
    try:
        c.create_bucket(Bucket=BUCKET)
        print("已创建桶 %s" % BUCKET)
    except Exception as e:
        print("建桶失败：%s" % e)
        sys.exit(1)

# ── Q1：virtual-hosted 寻址 ─────────────────────────────────────────────────
# media2doc 源码写死 virtual。但对 IP 端点，bucket.127.0.0.1 不是合法域名，
# 所以这里预期失败 —— 关键是确认「失败的方式」，据此决定是否要改 s3.py。
try:
    cv = make_client("virtual")
    cv.list_objects_v2(Bucket=BUCKET)
    record("Q1", "virtual-hosted 寻址可用", True,
           "可用 —— 但 IP 端点下这不常见，需确认解析到哪")
except Exception as e:
    record("Q1", "virtual-hosted 寻址可用", False,
           "%s（预期结果：IP 端点下 bucket.ip 无法解析 ⇒ 必须改用 path 风格）"
           % type(e).__name__)

# ── Q2/Q3：presigned PUT + GET（path 风格）──────────────────────────────────
KEY = "probe/verify-roundtrip.txt"
BODY = b"media2doc-local probe payload"

try:
    put_url = c.generate_presigned_url(
        "put_object", Params={"Bucket": BUCKET, "Key": KEY}, ExpiresIn=600
    )
    print("\npresigned PUT URL: %s" % put_url.split("?")[0])
    req = urllib.request.Request(put_url, data=BODY, method="PUT")
    with urllib.request.urlopen(req, timeout=30) as r:
        code = r.status
    record("Q2", "presigned PUT（浏览器直传模拟）", code in (200, 204),
           "HTTP %d，%d 字节已上传" % (code, len(BODY)))
except urllib.error.HTTPError as e:
    record("Q2", "presigned PUT（浏览器直传模拟）", False,
           "HTTP %d %s — %s" % (e.code, e.reason, e.read()[:200]))
except Exception as e:
    record("Q2", "presigned PUT（浏览器直传模拟）", False, "%s: %s" % (type(e).__name__, e))

try:
    get_url = c.generate_presigned_url(
        "get_object", Params={"Bucket": BUCKET, "Key": KEY}, ExpiresIn=600
    )
    with urllib.request.urlopen(get_url, timeout=30) as r:
        got = r.read()
    record("Q3", "presigned GET（ASR 取对象模拟）", got == BODY,
           "取回 %d 字节，内容%s" % (len(got), "一致" if got == BODY else "不一致"))
except Exception as e:
    record("Q3", "presigned GET（ASR 取对象模拟）", False, "%s: %s" % (type(e).__name__, e))

# ── Q4：后端 SDK 直读（ASR 用 s3.download_file 的等价物）────────────────────
try:
    obj = c.get_object(Bucket=BUCKET, Key=KEY)
    data = obj["Body"].read()
    record("Q4", "SDK 直取 get_object（ASR 实际用法）", data == BODY,
           "get_object 取回 %d 字节" % len(data))
except Exception as e:
    record("Q4", "SDK 直取 get_object（ASR 实际用法）", False, "%s: %s" % (type(e).__name__, e))

# ── 清理 ────────────────────────────────────────────────────────────────────
try:
    c.delete_object(Bucket=BUCKET, Key=KEY)
    print("\n已清理探测对象")
except Exception:
    pass

# ── 汇总 ────────────────────────────────────────────────────────────────────
print("\n" + "=" * 72)
passed = sum(1 for r in RESULTS if r["ok"])
print("结果：%d/%d 通过" % (passed, len(RESULTS)))
for r in RESULTS:
    print("  [%s] %s" % ("PASS" if r["ok"] else "FAIL", r["name"]))

print("\n结论：")
q2 = next(r for r in RESULTS if r["q"] == "Q2")
q3 = next(r for r in RESULTS if r["q"] == "Q3")
if q2["ok"] and q3["ok"]:
    print("  presigned PUT/GET 均可用 -> RustFS 满足 media2doc 的 S3 需求。")
    print("  [注意] 若 Q1 失败，说明必须改 backend\\utils\\s3.py 的")
    print("         addressing_style: virtual -> path（见 Q1 结果）。")
else:
    print("  [警告] presigned 不可用 -> RustFS 不能直接用，需另想办法。")
print("=" * 72)

with open(os.path.join(os.path.dirname(__file__), "verify_rustfs_s3.result.json"),
          "w", encoding="utf-8") as f:
    json.dump(RESULTS, f, ensure_ascii=False, indent=2)
