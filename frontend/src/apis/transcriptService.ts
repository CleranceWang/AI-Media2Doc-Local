/**
 * 转写产物服务 —— 桶里的 transcripts/{md5}.json 的前端入口。
 *
 * ═══════════════════════════════════════════════════════════════════════
 * 为什么需要它：原设计的转写文本只存在浏览器 IndexedDB 里
 * ═══════════════════════════════════════════════════════════════════════
 * utils/db.js 那套是纯本地的：清缓存、换浏览器、换设备，转写结果就没了。
 * 而转写是整个流程里**最贵的一步**（GPU 跑几十秒到几分钟），
 * 丢了就得重跑。更关键的是：没有持久化的转写文本，
 * 「命中哈希 → 跳过 ASR、只重跑 AI 总结」这条路径**根本无从实现**。
 *
 * 现在转写完成后由 ASR 服务直接落桶（src/asr/app.py:_run_transcription），
 * 本文件负责读回。查询顺序是「桶 → IndexedDB」：
 *   1) 桶 —— 跨设备可信，是事实来源
 *   2) IndexedDB —— 兜底，桶不可达时仍能工作（代价是只在本机有效）
 */
import httpService from './http'

/** 从音频 md5 拼出桶里的音频对象名。与 index.vue:139 保持一致。 */
export function audioKeyFromMd5(md5) {
  return `${md5}.mp3`
}

/** 从音频对象名反解出 md5。用于回到 IndexedDB 查询。 */
export function md5FromAudioKey(audioKey) {
  return String(audioKey || '').replace(/\.mp3$/i, '')
}

/**
 * 查桶里有没有这个音频的转写缓存。
 *
 * 返回 segments 数组，或 null（未命中）。
 *
 * **不抛异常**：查缓存失败与「没有缓存」对调用方的含义相同 ——
 * 都得老老实实跑一遍 ASR。为此炸掉整个流程是没有道理的。
 *
 * @param {string} audioKey 形如 `6db8a74c....mp3`
 * @returns {Promise<Array|null>}
 */
export async function getTranscriptFromBucket(audioKey) {
  try {
    const response = await httpService.get(`/api/v1/audio/transcripts/${encodeURIComponent(audioKey)}`)
    const data = response.data
    if (data && data.found && Array.isArray(data.result) && data.result.length > 0) {
      return data.result
    }
    return null
  } catch (error) {
    // 网络抖动、ASR 未起、老版本后端没有这个端点 —— 都走这里。
    // 降级成「没命中」，让流程继续走完整转写，而不是把用户卡住。
    console.warn('[transcript] 桶查询失败，将退回完整转写:', error?.message || error)
    return null
  }
}

/**
 * 删除某个音频在桶里的全部产物（音频 + 转写）。
 *
 * 幂等：对象本就不存在也算成功。
 * @returns {Promise<{success: boolean, deleted: string[]}>}
 */
export async function deleteTranscriptFromBucket(audioKey) {
  try {
    const response = await httpService.delete(`/api/v1/audio/transcripts/${encodeURIComponent(audioKey)}`)
    const data = response.data || {}
    return { success: true, deleted: data.deleted || [] }
  } catch (error) {
    console.warn('[transcript] 删除桶产物失败:', error?.message || error)
    return { success: false, deleted: [] }
  }
}

/** 列出桶里所有转写产物（跨设备的事实来源，可用于核对「名单」）。 */
export async function listBucketTranscripts() {
  try {
    const response = await httpService.get('/api/v1/audio/transcripts')
    const data = response.data || {}
    return data.items || []
  } catch (error) {
    console.warn('[transcript] 列举桶产物失败:', error?.message || error)
    return []
  }
}

/** 读取清理策略（保留期等），供设置页展示。 */
export async function getCleanupStatus() {
  try {
    const response = await httpService.get('/api/v1/audio/cleanup/status')
    return response.data || {}
  } catch (error) {
    console.warn('[transcript] 读取清理状态失败:', error?.message || error)
    return { enabled: null, retention_days: null }
  }
}

/** 立即触发一次过期清理。与定时任务共用同一份 purge_expired()。 */
export async function runCleanupNow() {
  const response = await httpService.post('/api/v1/audio/cleanup/run')
  return response.data || {}
}
