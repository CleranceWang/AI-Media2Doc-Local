# -*- coding: UTF-8 -*-
"""本地 ASR service —— 替代火山引擎 AUC。

设计要点（每条都对应一个已查明的约束）：

1) **保持异步两段式契约**
   原设计里火山 ASR 是「提交 → 轮询」，提交立即返回 task_id。
   而 faster-whisper 是同步的，一次 transcribe() 可能跑数秒到数分钟。
   若在提交请求里同步跑完，HTTP 会一直挂着，前端的轮询逻辑形同虚设，
   且前端 http.ts:30 的 timeout 是 240000ms（4 分钟），长音频会被冲破。
   ⇒ 本 service 自建「任务表 + 后台线程」，维持提交立即返回的语义。

2) **输出契约必须是毫秒**
   前端 index.vue:177-184 用 `seg.start_time / 60000` 和 `/ 1000` 换算，
   说明它期望的单位是**毫秒**。而 faster-whisper 的 segment 吐的是
   **秒（float）**。⇒ 必须 ×1000，否则时间戳差 1000 倍。

3) **自己去 S3 取对象，不用后端给的 presigned URL**
   presigned URL 里烧的 host 是后端视角的 127.0.0.1:9000，
   而在本容器内部 127.0.0.1 指向容器自己。⇒ 用自己配置的 S3_ENDPOINT
   （compose 里是 http://rustfs:9000）直接 get_object。

4) **ffmpeg 走容器内自带的**
   宿主机 PATH 上没有 ffmpeg（实测确认），所以不能依赖它。

5) **转写结果顺手落桶（transcripts/{md5}.json）**
   原设计里转写文本只留在浏览器 IndexedDB，换浏览器/清缓存就没了，
   且「命中哈希 → 复用转写、只重跑 AI 总结」这条路径无从实现。
   落桶的时机选在**转写完成的那一刻**，而不是等前端回调：
   即使浏览器随后崩了，转写结果也已经安全落地（转写是最贵的一步）。
   key 规则见 transcripts_key()，与音频对象共用同一个 md5 命名空间。
"""
import os
import io
import time
import uuid
import threading
import tempfile
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import boto3
from botocore.client import Config
from fastapi import FastAPI
from pydantic import BaseModel
from faster_whisper import WhisperModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("asr")

# ---------------------------------------------------------------- 配置
MODEL_DIR = os.getenv("ASR_MODEL_DIR", "/models/faster-whisper-large-v3")
DEVICE = os.getenv("ASR_DEVICE", "cuda")
COMPUTE_TYPE = os.getenv("ASR_COMPUTE_TYPE", "float16")

S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://rustfs:9000")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")
S3_REGION = os.getenv("S3_REGION", "us-east-1")
S3_BUCKET = os.getenv("S3_BUCKET", "media2doc")

# 转写结果在桶里的前缀。与音频对象（{md5}.mp3）分开命名空间，
# 便于「只清音频」「只清转写」这类运维操作，也让桶里一眼能分清两类产物。
TRANSCRIPT_PREFIX = os.getenv("TRANSCRIPT_PREFIX", "transcripts")

# ---------------------------------------------------------------- 过期清理
# 桶会随使用单调增长：每跑一个视频，就多一份 mp3 + 一份转写 JSON。
# 挂个定时器按「对象年龄」回收，而不是做手动「清空」按钮 ——
# 桶膨胀是慢性问题，靠人记得去点按钮，实际等于不会清。
#
# 保留期以**天**为单位，0 或负数表示关闭清理（想长期留档时用）。
# 桶内产物（音频 + 转写 JSON）的寿命，单位天。
# 语义：**按对象寿命逐个判定**，不是「到期清空整个桶」——
#   寿命 = 0（或负数）→ 清理关闭，桶只增不减
#   寿命 > 0         → 只删 LastModified 早于「现在 - 寿命」的对象
# 为什么 0 表示关闭而不是「立即过期」：两者字面很像，但后果差一个数量级。
# 把 0 解释成「立刻全删」，用户手滑填 0 就会丢掉整个桶；解释成「关闭」最坏只是不清理。
RETENTION_DAYS = float(os.getenv("RETENTION_DAYS", "1"))

# 清理扫描间隔（秒）。默认 1 小时 —— 清理不是实时任务，
# 扫得太勤只是白白增加 S3 请求量。
CLEANUP_INTERVAL = float(os.getenv("CLEANUP_INTERVAL_SECONDS", "3600"))

# 启动后延迟多久开始第一次清理。默认 5 分钟，
# 避开容器冷启动时的模型加载 / 首批请求，别跟正事抢 IO。
CLEANUP_INITIAL_DELAY = float(os.getenv("CLEANUP_INITIAL_DELAY_SECONDS", "300"))

app = FastAPI(title="media2doc-local ASR")


def transcript_key(audio_key: str) -> str:
    """音频对象名 → 转写结果对象名。

    audio_key 形如 `6db8a74cf1453fbbcd4b75456f3e3a37.mp3`（前端 index.vue:139
    用音频 md5 作文件名），对应 transcripts/6db8a74cf1453fbbcd4b75456f3e3a37.json。
    刻意复用同一个 md5 —— 音频和转写结果是同一个哈希的两个侧面，
    换命名规则会让「按哈希找转写」多一步映射，徒增出错面。
    """
    stem = os.path.splitext(os.path.basename(audio_key))[0]
    return "%s/%s.json" % (TRANSCRIPT_PREFIX, stem)

# ---------------------------------------------------------------- 模型（单例）
# 懒加载 + 全局锁：模型加载要几十秒且吃显存，不能让并发请求各加载一份。
_model: Optional[WhisperModel] = None
_model_lock = threading.Lock()


def get_model() -> WhisperModel:
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:  # double-checked locking
                logger.info("加载模型 %s (device=%s, compute=%s)", MODEL_DIR, DEVICE, COMPUTE_TYPE)
                t0 = time.time()
                _model = WhisperModel(MODEL_DIR, device=DEVICE, compute_type=COMPUTE_TYPE)
                logger.info("模型加载完成，耗时 %.1fs", time.time() - t0)
    return _model


# ---------------------------------------------------------------- S3 客户端
def get_s3():
    return boto3.client(
        "s3",
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        endpoint_url=S3_ENDPOINT,
        region_name=S3_REGION,
        # 本地是明文 http；verify=True 在 http 下无害，但语义上应关掉
        verify=False,
        config=Config(
            signature_version="s3v4",
            # ★ path 风格：避免 bucket.127.0.0.1 这种无法解析的域名
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


# ---------------------------------------------------------------- 任务表
# 内存态即可：单机、单进程、任务生命周期短。
# ⚠️ 若将来上多副本，这张表必须换成 Redis 之类，否则轮询会打不到正确的实例。
TASKS: Dict[str, Dict[str, Any]] = {}

# 状态值对齐 constants.py 的 AsrTaskStatus
STATUS_RUNNING = "running"
STATUS_FINISHED = "finished"
STATUS_FAILED = "failed"


class TranscribeRequest(BaseModel):
    """请求体。

    filename 与后端 files.py 的 FileNameRequest 同名，保持语义一致。
    """
    filename: str


def _run_transcription(task_id: str, filename: str) -> None:
    """后台线程：从 S3 取音频 → 转写 → 写回任务表。"""
    try:
        logger.info("[%s] 开始处理 %s", task_id, filename)

        # 1) 取音频。用临时文件而非内存 —— large-v3 处理长音频时
        #    ffmpeg 需要可 seek 的输入，io.BytesIO 某些情况下会出问题。
        s3 = get_s3()
        suffix = os.path.splitext(filename)[1] or ".mp3"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
            tmp_path = tf.name

        try:
            s3.download_file(S3_BUCKET, filename, tmp_path)
            logger.info("[%s] 已下载到 %s", task_id, tmp_path)

            # 2) 转写
            model = get_model()
            segments, info = model.transcribe(tmp_path, beam_size=5)
            logger.info(
                "[%s] 检测语言=%s 概率=%.2f 时长=%.1fs",
                task_id, info.language, info.language_probability, info.duration,
            )

            # 3) ★ 契约转换：秒(float) → 毫秒(int)
            result = [
                {
                    "start_time": int(seg.start * 1000),
                    "end_time": int(seg.end * 1000),
                    "text": seg.text.strip(),
                }
                for seg in segments
            ]

            # 3.5) ★ 转写结果落桶。
            # 失败**不阻断**任务：转写本身已经成功了，桶写不进去只是丢了
            # 复用能力，不该把这个代价转嫁成「转写失败」。所以这里只记日志。
            try:
                import json as _json
                key = transcript_key(filename)
                s3.put_object(
                    Bucket=S3_BUCKET,
                    Key=key,
                    Body=_json.dumps(
                        {"filename": filename, "language": info.language,
                         "duration": info.duration, "result": result},
                        ensure_ascii=False,
                    ).encode("utf-8"),
                    ContentType="application/json; charset=utf-8",
                )
                logger.info("[%s] 转写结果已落桶 %s", task_id, key)
            except Exception as e:
                logger.warning("[%s] 转写结果落桶失败（不影响转写）: %s", task_id, e)

            TASKS[task_id] = {
                "status": STATUS_FINISHED,
                "result": result,
                "error": None,
            }
            logger.info("[%s] 完成，%d 段", task_id, len(result))

        finally:
            # 无论成败都要清临时文件
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    except Exception as e:
        logger.exception("[%s] 处理失败", task_id)
        TASKS[task_id] = {
            "status": STATUS_FAILED,
            "result": None,
            "error": "%s: %s" % (type(e).__name__, e),
        }


# ---------------------------------------------------------------- 过期清理
def purge_expired() -> Dict[str, Any]:
    """删除桶里超过 RETENTION_DAYS 未修改的对象。

    用 LastModified 而非对象名/创建时间来判年龄：LastModified 由 S3 侧维护，
    是**唯一可信的时间源** —— 本地时钟、容器时区都可能不对。

    同时清 mp3（根目录）和转写 JSON（transcripts/ 前缀），
    否则会留下「转写在、音频没了」的半残状态，比留着更糟：
    命中哈希后去取音频会 404，而错误点离真正的原因很远。

    只删、不抛异常：清理是尽力而为的后台任务，
    一次失败不该让整个定时器停摆。
    """
    if RETENTION_DAYS <= 0:
        return {"enabled": False, "reason": "RETENTION_DAYS<=0，清理已关闭"}

    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    s3 = get_s3()
    deleted, errors, scanned = [], [], 0

    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=S3_BUCKET):
            for obj in page.get("Contents", []):
                scanned += 1
                if obj["LastModified"] >= cutoff:
                    continue
                try:
                    s3.delete_object(Bucket=S3_BUCKET, Key=obj["Key"])
                    deleted.append(obj["Key"])
                except Exception as e:
                    errors.append("%s: %s" % (obj["Key"], e))
    except Exception as e:
        logger.warning("清理时列举桶失败: %s", e)
        errors.append("list: %s" % e)

    if deleted:
        logger.info(
            "清理完成：扫描 %d，删除 %d，保留期 %.1f 天",
            scanned, len(deleted), RETENTION_DAYS,
        )
    return {
        "enabled": True,
        "scanned": scanned,
        "deleted": deleted,
        "errors": errors,
        "retention_days": RETENTION_DAYS,
    }


def _cleanup_loop() -> None:
    """后台清理线程：先等一会，然后每隔 CLEANUP_INTERVAL 扫一次。"""
    time.sleep(CLEANUP_INITIAL_DELAY)
    while True:
        try:
            purge_expired()
        except Exception:
            # 定时器绝不能因单次异常而死掉 —— 它一死，桶就再也无人回收。
            logger.exception("清理任务异常（定时器继续）")
        time.sleep(CLEANUP_INTERVAL)


@app.on_event("startup")
def start_cleanup_timer():
    if RETENTION_DAYS <= 0:
        logger.info("桶清理已关闭（RETENTION_DAYS=%.1f）", RETENTION_DAYS)
        return
    t = threading.Thread(target=_cleanup_loop, daemon=True, name="bucket-cleanup")
    t.start()
    logger.info(
        "桶清理已启动：保留 %.1f 天，每 %.0f 秒扫一次，首次延迟 %.0f 秒",
        RETENTION_DAYS, CLEANUP_INTERVAL, CLEANUP_INITIAL_DELAY,
    )


# ---------------------------------------------------------------- 路由
@app.post("/transcribe")
def create_task(req: TranscribeRequest):
    """提交转写任务，**立即**返回 task_id（不阻塞）。

    对应后端要替换的 POST /api/v1/audio/transcription-tasks。
    """
    task_id = uuid.uuid4().hex
    TASKS[task_id] = {"status": STATUS_RUNNING, "result": None, "error": None}

    t = threading.Thread(target=_run_transcription, args=(task_id, req.filename), daemon=True)
    t.start()

    logger.info("已受理任务 %s -> %s", task_id, req.filename)
    return {"task_id": task_id}


@app.get("/transcribe/{task_id}")
def get_task(task_id: str):
    """查询任务状态。

    对应后端要替换的 GET /api/v1/audio/transcription-tasks/{task_id}。
    """
    info = TASKS.get(task_id)
    if info is None:
        # 未知 task_id 按失败处理，与火山侧「查不到即失败」的语义一致
        return {"status": STATUS_FAILED, "result": None, "error": "unknown task_id"}

    return {
        "status": info["status"],
        "result": info["result"],
        "error": info["error"],
    }


@app.get("/health")
def health():
    """探活。顺带报告模型是否已加载 —— 模型没加载完就派任务会排队。"""
    return {
        "status": "ok",
        "model_loaded": _model is not None,
        "model_dir": MODEL_DIR,
        "device": DEVICE,
        "compute_type": COMPUTE_TYPE,
        "tasks": len(TASKS),
    }


# ---------------------------------------------------------------- 转写结果复用
@app.get("/transcript/{audio_key}")
def get_transcript(audio_key: str):
    """按音频对象名取回已缓存的转写结果。

    这是「命中哈希 → 跳过 ASR、直接重跑 AI 总结」那条路径的数据来源。
    前端算完音频 md5 后，先问这里；拿到 200 就不必再走一遍 GPU 转写。

    返回 200 + 结果，或 404（桶里没有）。**刻意不返回 5xx** ——
    「没有缓存」是正常业务状态，不是错误，前端不该为此弹报错。
    """
    key = transcript_key(audio_key)
    try:
        obj = get_s3().get_object(Bucket=S3_BUCKET, Key=key)
        import json as _json
        payload = _json.loads(obj["Body"].read().decode("utf-8"))
        return {
            "found": True,
            "key": key,
            "result": payload.get("result", []),
            "language": payload.get("language"),
            "duration": payload.get("duration"),
        }
    except Exception as e:
        # NoSuchKey / 404 都走这里。区分太细没有意义，前端只关心「有没有」。
        logger.info("转写缓存未命中 %s: %s", key, e)
        return {"found": False, "key": key, "result": None}


@app.delete("/transcript/{audio_key}")
def delete_transcript(audio_key: str):
    """删除某个音频对应的**全部**桶内产物（音频 + 转写）。

    对应前端「删除任务记录」的动作 —— 原设计只删浏览器 IndexedDB，
    桶里的 mp3 和转写 JSON 会永远残留，桶越用越脏。
    这里做成幂等：对象不存在也算成功（删除的目标是「确保它没了」，
    而不是「证明它曾经在」）。
    """
    keys = [audio_key, transcript_key(audio_key)]
    deleted, failed = [], []
    s3 = get_s3()
    for k in keys:
        try:
            s3.delete_object(Bucket=S3_BUCKET, Key=k)
            deleted.append(k)
        except Exception as e:
            failed.append("%s: %s" % (k, e))

    logger.info("删除任务产物 %s -> deleted=%s failed=%s", audio_key, deleted, failed)
    return {
        "success": len(failed) == 0,
        "deleted": deleted,
        "failed": failed,
    }


@app.get("/transcripts")
def list_transcripts():
    """列出桶里所有转写产物，供「名单/列表」核对用。

    这是**只读的运维视图** —— 前端那套 IndexedDB 是浏览器本地状态，
    清缓存/换浏览器就没了；桶里的这份才是跨设备的事实来源。
    """
    try:
        s3 = get_s3()
        resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=TRANSCRIPT_PREFIX + "/")
        items = [
            {"key": o["Key"], "size": o["Size"], "modified": o["LastModified"].isoformat()}
            for o in resp.get("Contents", [])
        ]
        return {"found": True, "count": len(items), "items": items}
    except Exception as e:
        logger.warning("列举转写产物失败: %s", e)
        return {"found": False, "count": 0, "items": [], "error": str(e)}


# ---------------------------------------------------------------- 清理的运维入口
@app.get("/cleanup/status")
def cleanup_status():
    """报告清理配置，让「设置」页能显示当前策略而不必去读代码。"""
    return {
        "enabled": RETENTION_DAYS > 0,
        "retention_days": RETENTION_DAYS,
        "interval_seconds": CLEANUP_INTERVAL,
        "initial_delay_seconds": CLEANUP_INITIAL_DELAY,
    }


@app.post("/cleanup/run")
def cleanup_run():
    """手动触发一次清理。

    定时器之外留这个入口，是为了让「设置」页能提供「立即清理」，
    也让排查时不必等一个小时。与定时任务共用同一份 purge_expired()，
    不存在两套逻辑走偏的可能。
    """
    return purge_expired()
