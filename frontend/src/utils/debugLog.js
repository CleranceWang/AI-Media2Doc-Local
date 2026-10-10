/**
 * debugLog.js —— 前端调试日志收集器
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * 安装方法（本文件是补丁，不会自动生效）
 * ═══════════════════════════════════════════════════════════════════════════
 * apply_patches.py 会：
 *   1. 把本文件复制到  frontend\src\utils\debugLog.js
 *   2. 把 DebugPanel.vue 复制到  frontend\src\components\DebugPanel.vue
 *   3. 在 main.js 里注入 `installDebugCapture()` 调用
 *   4. 在 App.vue 里注入 `<DebugPanel />`
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * 设计原则：**旁路监听，不改业务代码**
 * ═══════════════════════════════════════════════════════════════════════════
 * 本模块在 main.js 里被 import 一次即自动生效，通过猴补丁（monkey patch）
 * 拦截三类信息源：
 *
 *   1. fetch()          —— 后端 API 调用
 *   2. XMLHttpRequest   —— ★ 关键：浏览器直传 S3 用的是 XHR（http.ts:150）
 *   3. console.* + window.onerror + unhandledrejection
 *
 * 为什么必须拦 XHR：media2doc 上传音频走的是
 *     frontend\src\apis\http.ts:150    xhr.open('PUT', url)
 * 而 CORS 失败**恰恰发生在这一层** —— 浏览器会因为缺少
 * Access-Control-Allow-Origin 而掐断请求，XHR 收到的是笼统的
 * "network error"，用 fetch 拦截根本看不到。这个坑已实测踩过。
 *
 * 也因此，本模块特意把上传失败时的**排查提示**写进日志 ——
 * 光看 "network error" 无法区分「网络不通」「CORS 被拒」「签名错误」。
 * ═══════════════════════════════════════════════════════════════════════════
 */

import { reactive } from 'vue'

const MAX_ENTRIES = 500

export const debugState = reactive({
    enabled: true,
    entries: [],
    seq: 0,
})

function now() {
    const d = new Date()
    const p = (n, w = 2) => String(n).padStart(w, '0')
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`
}

/**
 * 记录一条日志。
 * @param {'api'|'xhr'|'console'|'error'|'info'} kind
 * @param {string} title
 * @param {object} [detail]
 */
export function addEntry(kind, title, detail = {}) {
    if (!debugState.enabled) return
    debugState.entries.push({
        id: ++debugState.seq,
        time: now(),
        kind,
        title,
        detail,
    })
    // 环形缓冲：只留最近 MAX_ENTRIES 条，避免长时间开着吃内存
    if (debugState.entries.length > MAX_ENTRIES) {
        debugState.entries.splice(0, debugState.entries.length - MAX_ENTRIES)
    }
}

export function clearEntries() {
    debugState.entries.splice(0, debugState.entries.length)
    debugState.seq = 0
}

/* ------------------------------------------------------------------ *
 * 诊断辅助：把笼统的错误翻译成可操作的提示
 * ------------------------------------------------------------------ */

/**
 * XHR 出错时，status 为 0 是最常见也最含糊的情况。
 * 它可能是：CORS 被拒 / 连接被拒 / DNS 失败 / 用户取消 / 混合内容拦截。
 */
function diagnoseXhr(xhr, url, method) {
    if (xhr.status !== 0) return null

    const hints = []
    const isS3 = /:9000\//.test(url)
    const isLocalhost = /127\.0\.0\.1|localhost/.test(url)

    if (isS3) {
        hints.push('目标看起来是 S3 直传端点')
        hints.push('最常见原因：桶的 CORS 未配置（浏览器预检拿不到 Access-Control-Allow-Origin）')
        hints.push('排查：python scripts\\init_rustfs.py  然后刷新页面重试')
        hints.push('注意：命令行 curl / Invoke-WebRequest 不做 CORS 检查，它们能成功不代表浏览器能成功')
    }
    if (isLocalhost) {
        hints.push('确认对应容器在跑：docker ps --filter name=m2d-')
    }
    if (!navigator.onLine) {
        hints.push('浏览器报告离线')
    }
    if (location.protocol === 'https:' && url.startsWith('http:')) {
        hints.push('混合内容：HTTPS 页面请求 HTTP 资源会被浏览器拦截')
    }
    return hints
}

function truncate(s, n = 600) {
    if (typeof s !== 'string') {
        try { s = JSON.stringify(s) } catch { s = String(s) }
    }
    return s.length > n ? s.slice(0, n) + `… (+${s.length - n} 字符)` : s
}

/* ------------------------------------------------------------------ *
 * 安装拦截器
 * ------------------------------------------------------------------ */

let installed = false

export function installDebugCapture() {
    if (installed) return
    installed = true

    /* ---- 1. fetch ---- */
    const origFetch = window.fetch
    if (origFetch) {
        window.fetch = async function (...args) {
            const input = args[0]
            const init = args[1] || {}
            const url = typeof input === 'string' ? input : (input && input.url) || String(input)
            const method = (init.method || (input && input.method) || 'GET').toUpperCase()
            const t0 = performance.now()
            try {
                const resp = await origFetch.apply(this, args)
                const ms = Math.round(performance.now() - t0)
                addEntry('api', `${method} ${resp.status} ${shortUrl(url)}`, {
                    耗时: `${ms}ms`,
                    url,
                    status: resp.status,
                    ok: resp.ok,
                })
                return resp
            } catch (e) {
                const ms = Math.round(performance.now() - t0)
                addEntry('error', `fetch 失败 ${method} ${shortUrl(url)}`, {
                    耗时: `${ms}ms`,
                    url,
                    错误: String(e && e.message || e),
                    提示: diagnoseXhr({ status: 0 }, url, method),
                })
                throw e
            }
        }
    }

    /* ---- 2. XMLHttpRequest（★ 上传走这里）---- */
    const origOpen = XMLHttpRequest.prototype.open
    const origSend = XMLHttpRequest.prototype.send

    XMLHttpRequest.prototype.open = function (method, url, ...rest) {
        this.__dbg = { method: (method || 'GET').toUpperCase(), url: String(url), t0: 0 }
        return origOpen.call(this, method, url, ...rest)
    }

    XMLHttpRequest.prototype.send = function (...args) {
        const info = this.__dbg
        if (info) {
            info.t0 = performance.now()
            // 记录请求体大小 —— 上传失败的常见背景是文件太大
            try {
                const b = args[0]
                info.bodySize = (b && (b.size ?? b.byteLength)) ?? null
            } catch { info.bodySize = null }

            this.addEventListener('loadend', () => {
                const ms = Math.round(performance.now() - info.t0)
                const ok = this.status >= 200 && this.status < 300
                const detail = {
                    耗时: `${ms}ms`,
                    url: info.url,
                    status: this.status,
                    statusText: this.statusText,
                }
                if (info.bodySize != null) detail['请求体'] = `${info.bodySize} 字节`

                if (ok) {
                    addEntry('xhr', `${info.method} ${this.status} ${shortUrl(info.url)}`, detail)
                } else {
                    // 读响应体有助于区分 CORS / 签名 / 权限错误
                    try {
                        const ct = this.getResponseHeader('content-type') || ''
                        if (this.responseText && this.responseText.length < 2000) {
                            detail['响应体'] = truncate(this.responseText)
                        } else if (!ct) {
                            detail['响应体'] = '(空 —— status 0 且无响应体是 CORS 被拒的典型特征)'
                        }
                    } catch {
                        detail['响应体'] = '(不可读 —— 跨源且未授权，典型 CORS 表现)'
                    }
                    const hints = diagnoseXhr(this, info.url, info.method)
                    if (hints) detail['提示'] = hints
                    addEntry('error', `${info.method} ${this.status || '被拦截'} ${shortUrl(info.url)}`, detail)
                }
            })
        }
        return origSend.apply(this, args)
    }

    /* ---- 3. console ---- */
    const levels = ['log', 'info', 'warn', 'error']
    levels.forEach((level) => {
        const orig = console[level]
        if (!orig) return
        console[level] = function (...args) {
            try {
                const text = args.map((a) => {
                    if (typeof a === 'string') return a
                    if (a instanceof Error) return `${a.name}: ${a.message}`
                    try { return JSON.stringify(a) } catch { return String(a) }
                }).join(' ')
                // 过滤掉自己产生的噪音
                if (!text.includes('[debug-panel]')) {
                    addEntry('console', `[${level}] ${truncate(text, 300)}`, {
                        原文: truncate(text, 2000),
                    })
                }
            } catch { /* 记录日志本身绝不能抛错 */ }
            return orig.apply(console, args)
        }
    })

    /* ---- 4. 全局错误 ---- */
    window.addEventListener('error', (e) => {
        addEntry('error', '未捕获错误', {
            消息: e.message,
            来源: `${e.filename}:${e.lineno}:${e.colno}`,
            堆栈: e.error && e.error.stack ? truncate(e.error.stack, 1500) : null,
        })
    })

    window.addEventListener('unhandledrejection', (e) => {
        const r = e.reason
        addEntry('error', '未处理的 Promise 拒绝', {
            原因: r instanceof Error ? `${r.name}: ${r.message}` : String(r),
            堆栈: r && r.stack ? truncate(r.stack, 1500) : null,
        })
    })

    addEntry('info', '调试面板已启用，开始采集', {
        说明: '拦截 fetch / XMLHttpRequest / console / 全局错误',
        页面: location.href,
    })
}

function shortUrl(u) {
    try {
        const x = new URL(u, location.href)
        const path = x.pathname.length > 60 ? x.pathname.slice(0, 60) + '…' : x.pathname
        return `${x.host}${path}`
    } catch {
        return String(u).slice(0, 80)
    }
}

/**
 * 生成可直接粘贴的纯文本报告。
 */
export function buildReport() {
    const lines = []
    lines.push('='.repeat(70))
    lines.push('media2doc-local 前端调试报告')
    lines.push(`导出时间: ${new Date().toISOString()}`)
    lines.push(`页面地址: ${location.href}`)
    lines.push(`浏览器  : ${navigator.userAgent}`)
    lines.push(`在线状态: ${navigator.onLine}`)
    lines.push(`日志条数: ${debugState.entries.length}`)
    lines.push('='.repeat(70))
    lines.push('')

    for (const e of debugState.entries) {
        lines.push(`[${e.time}] (${e.kind}) ${e.title}`)
        const d = e.detail || {}
        for (const k of Object.keys(d)) {
            const v = d[k]
            if (v == null || v === '') continue
            if (Array.isArray(v)) {
                v.forEach((item) => lines.push(`    ${k}: ${item}`))
            } else {
                lines.push(`    ${k}: ${v}`)
            }
        }
        lines.push('')
    }
    return lines.join('\n')
}
