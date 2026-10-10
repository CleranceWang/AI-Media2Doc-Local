# -*- coding: UTF-8 -*-
"""替换版 routers/audio.py —— 把火山引擎 AUC 换成本地 ASR service。

═══════════════════════════════════════════════════════════════════════════════
改动原则：**对外契约一字不改**，前端零改动
═══════════════════════════════════════════════════════════════════════════════
保留原有两个端点与其返回形状：
    POST /api/v1/audio/transcription-tasks           → {"data": {"task_id": ...}}
    GET  /api/v1/audio/transcription-tasks/{task_id} → {"data": {"status": ..., "result": [...]}}

前端消费点（已逐行核对）：
    · index.vue:163-164   submitAsrTask → pollAsrTask
    · index.vue:174-187   读 seg.start_time / seg.end_time / seg.text（**毫秒**）
    · TranscriptionPanel.vue:42  显式断言 'start_time' in t[0] && 'text' in t[0]
    · types.ts:66-67      start_time: number; end_time: number

状态映射（对齐 constants.py 的 AsrTaskStatus）：
    本地 running  → AsrTaskStatus.RUNNING
    本地 finished → AsrTaskStatus.FINISHED
    本地 failed   → AsrTaskStatus.FAILED
═══════════════════════════════════════════════════════════════════════════════
"""
from fastapi import APIRouter
import requests

from constants import AsrTaskStatus
from models import FileNameRequest
from core.exceptions import BusinessException, ExternalServiceException
from core.response import success_response, APIResponse
from config.log import get_logger
import env

router = APIRouter(prefix="/audio", tags=["Audio"])
logger = get_logger(__name__)

# 本地 ASR service 的基址。默认值对应 compose 里的 service 名。
ASR_BASE_URL = getattr(env, "ASR_BASE_URL", None) or "http://asr:8000"

# 与前端 http.ts:30 的 timeout（240000ms）保持同一量级，
# 但这是「单次查询」的超时，不是「等转写完成」的超时 —— 查询本身应该秒回。
_QUERY_TIMEOUT = 30


def _map_status(local_status: str) -> str:
    """本地状态 → 契约状态。未知值一律按 RUNNING 处理，让前端继续轮询。

    这样比直接判 FAILED 更稳：一次瞬时异常不会让整个任务被判死。
    """
    return {
        "finished": AsrTaskStatus.FINISHED.value,
        "failed": AsrTaskStatus.FAILED.value,
        "running": AsrTaskStatus.RUNNING.value,
    }.get(local_status, AsrTaskStatus.RUNNING.value)


@router.post("/transcription-tasks", response_model=APIResponse)
async def create_transcription_task(request: FileNameRequest):
    """创建音频转写任务（提交到本地 ASR，立即返回 task_id）。

    RESTful路径: POST /api/v1/audio/transcription-tasks

    注意：这里**不再**生成 presigned download URL。
    原实现是 generate_download_url() 把 URL 交给云端火山，因为火山不收文件、
    只收一个「云端够得着的 URL」；换成本地 ASR 后音频就在本机，
    ASR 自己用容器内可达的 S3 地址取对象，「公网 URL」这个环节整个消失。
    """
    logger.info(f"Creating transcription task for file: {request.filename}")

    try:
        resp = requests.post(
            f"{ASR_BASE_URL}/transcribe",
            json={"filename": request.filename},
            timeout=_QUERY_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()

        task_id = payload["task_id"]
        logger.info(f"Local ASR accepted task {task_id} for {request.filename}")

        return success_response(
            data={"task_id": task_id},
            message="Transcription task created successfully",
        )

    except requests.RequestException as e:
        logger.error(f"Failed to reach local ASR service: {e}")
        raise ExternalServiceException("LocalASR", f"Request failed: {e}")
    except Exception as e:
        logger.error(f"Unexpected error when creating transcription task: {e}")
        raise BusinessException(f"Failed to create transcription task: {e}")


@router.get("/transcription-tasks/{task_id}", response_model=APIResponse)
async def get_transcription_task(task_id: str):
    """获取音频转写任务状态（查询本地 ASR）。

    RESTful路径: GET /api/v1/audio/transcription-tasks/{task_id}
    """
    logger.info(f"Querying transcription task status: {task_id}")

    try:
        resp = requests.get(
            f"{ASR_BASE_URL}/transcribe/{task_id}",
            timeout=_QUERY_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()

        status = _map_status(payload.get("status"))
        result = payload.get("result")

        if status == AsrTaskStatus.FINISHED.value:
            logger.info(f"Transcription task {task_id} finished with {len(result or [])} segments")
        elif status == AsrTaskStatus.FAILED.value:
            logger.error(f"Transcription task {task_id} failed: {payload.get('error')}")

        return success_response(
            data={"status": status, "result": result},
            message="Transcription status retrieved",
        )

    except requests.RequestException as e:
        # 查询失败不判死任务：返回 RUNNING，让前端下次再问。
        # 本地 ASR 若真的挂了，会持续返回 RUNNING，比误报 FAILED 更容易排查。
        logger.error(f"Failed to query local ASR service for {task_id}: {e}")
        return success_response(
            data={"status": AsrTaskStatus.RUNNING.value, "result": None},
            message="Transcription service temporarily unreachable",
        )
    except Exception as e:
        logger.error(f"Unexpected error when querying transcription task {task_id}: {e}")
        raise BusinessException(f"Failed to query transcription task: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# 转写结果缓存（桶里的 transcripts/{md5}.json）
# ═══════════════════════════════════════════════════════════════════════════════
# 原设计的哈希去重只查浏览器 IndexedDB（utils/db.js），换浏览器/清缓存即失效，
# 且转写产物本身没落盘 —— 命中后也无从复用，只能重跑一遍 GPU。
# 落桶后，「命中哈希 → 跳过 ASR、只重跑 AI 总结」才有数据基础。
#
# 归属说明：这三个端点读写的是**转写产物**，语义上属于 audio 域而非 files 域，
# 所以放在本文件而不是 files.py。files.py 只管上传 URL 签发。


@router.get("/transcripts/{audio_key}", response_model=APIResponse)
async def get_cached_transcript(audio_key: str):
    """按音频对象名取回缓存的转写结果。

    RESTful路径: GET /api/v1/audio/transcripts/{audio_key}

    audio_key 是前端算出的「{音频md5}.mp3」（index.vue:139）。
    未命中时 found=false —— 这是正常业务状态，不是错误，**不抛异常**，
    否则前端会把它当成失败弹报错，而实际上只意味着「得重新转写」。
    """
    logger.info(f"Looking up cached transcript for: {audio_key}")
    try:
        resp = requests.get(f"{ASR_BASE_URL}/transcript/{audio_key}", timeout=_QUERY_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        return success_response(
            data={
                "found": bool(payload.get("found")),
                "result": payload.get("result"),
                "language": payload.get("language"),
                "duration": payload.get("duration"),
            },
            message="Transcript cache lookup completed",
        )
    except requests.RequestException as e:
        # 查缓存失败不该阻断流程：当作「没命中」，让前端正常走完整转写。
        logger.warning(f"Transcript cache lookup failed for {audio_key}: {e}")
        return success_response(
            data={"found": False, "result": None},
            message="Transcript cache unavailable, fall back to full transcription",
        )
    except Exception as e:
        logger.error(f"Unexpected error looking up transcript {audio_key}: {e}")
        raise BusinessException(f"Failed to look up transcript: {e}")


@router.delete("/transcripts/{audio_key}", response_model=APIResponse)
async def delete_transcript_assets(audio_key: str):
    """删除某个音频在桶里的全部产物（音频 + 转写）。

    RESTful路径: DELETE /api/v1/audio/transcripts/{audio_key}

    幂等：对象本来就不存在也算成功。删除的目标是「确保它没了」。
    """
    logger.info(f"Deleting bucket assets for: {audio_key}")
    try:
        resp = requests.delete(f"{ASR_BASE_URL}/transcript/{audio_key}", timeout=_QUERY_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        return success_response(
            data={
                "deleted": payload.get("deleted", []),
                "failed": payload.get("failed", []),
            },
            message="Bucket assets deleted",
        )
    except requests.RequestException as e:
        logger.error(f"Failed to delete bucket assets for {audio_key}: {e}")
        raise ExternalServiceException("LocalASR", f"Delete failed: {e}")
    except Exception as e:
        logger.error(f"Unexpected error deleting bucket assets {audio_key}: {e}")
        raise BusinessException(f"Failed to delete bucket assets: {e}")


@router.get("/transcripts", response_model=APIResponse)
async def list_transcripts_api():
    """列出桶里所有转写产物。

    RESTful路径: GET /api/v1/audio/transcripts

    这是跨设备的事实来源 —— IndexedDB 那份是浏览器本地的，会丢。
    """
    logger.info("Listing all cached transcripts")
    try:
        resp = requests.get(f"{ASR_BASE_URL}/transcripts", timeout=_QUERY_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        return success_response(
            data={"count": payload.get("count", 0), "items": payload.get("items", [])},
            message="Transcript list retrieved",
        )
    except requests.RequestException as e:
        logger.error(f"Failed to list transcripts: {e}")
        raise ExternalServiceException("LocalASR", f"List failed: {e}")
    except Exception as e:
        logger.error(f"Unexpected error listing transcripts: {e}")
        raise BusinessException(f"Failed to list transcripts: {e}")


# ───────────────────────────────────────────────────────────────────────────
# 桶清理（定时器跑在 ASR 容器里，这里只做观测与手动触发）
# ───────────────────────────────────────────────────────────────────────────
@router.get("/cleanup/status", response_model=APIResponse)
async def get_cleanup_status():
    """读取清理策略，供「设置」页展示当前保留期。

    RESTful路径: GET /api/v1/audio/cleanup/status
    """
    try:
        resp = requests.get(f"{ASR_BASE_URL}/cleanup/status", timeout=_QUERY_TIMEOUT)
        resp.raise_for_status()
        return success_response(data=resp.json(), message="Cleanup status retrieved")
    except requests.RequestException as e:
        # 老版本 ASR 镜像没有这个端点 —— 返回「未知」而不是炸掉设置页。
        logger.warning(f"Cleanup status unavailable: {e}")
        return success_response(
            data={"enabled": None, "retention_days": None},
            message="Cleanup status unavailable (ASR service may be outdated)",
        )
    except Exception as e:
        logger.error(f"Unexpected error reading cleanup status: {e}")
        raise BusinessException(f"Failed to read cleanup status: {e}")


@router.post("/cleanup/run", response_model=APIResponse)
async def run_cleanup_now():
    """立即触发一次过期清理。

    RESTful路径: POST /api/v1/audio/cleanup/run

    与定时任务共用同一份 purge_expired()，不存在两套逻辑走偏的可能 ——
    这也是留这个入口的主要动机（而不是重写一遍删除逻辑）。
    """
    logger.info("Manual cleanup triggered")
    try:
        resp = requests.post(f"{ASR_BASE_URL}/cleanup/run", timeout=120)
        resp.raise_for_status()
        payload = resp.json()
        logger.info(f"Cleanup done: deleted={len(payload.get('deleted', []))}")
        return success_response(data=payload, message="Cleanup executed")
    except requests.RequestException as e:
        logger.error(f"Manual cleanup failed: {e}")
        raise ExternalServiceException("LocalASR", f"Cleanup failed: {e}")
    except Exception as e:
        logger.error(f"Unexpected error during cleanup: {e}")
        raise BusinessException(f"Failed to run cleanup: {e}")
