<script setup>
/**
 * DebugPanel.vue —— 调试面板（右下角悬浮球）
 *
 * 用途：出问题时一键复制完整日志，便于定位。
 * 见 utils/debugLog.js 的头部说明了解采集范围与设计取舍。
 */
import { ref, computed, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { debugState, clearEntries, buildReport } from '../utils/debugLog'

const isOpen = ref(false)
const filter = ref('all')
const listRef = ref(null)

const filters = [
    { key: 'all', label: '全部' },
    { key: 'error', label: '错误' },
    { key: 'xhr', label: 'XHR' },
    { key: 'api', label: 'API' },
    { key: 'console', label: '控制台' },
]

const filtered = computed(() => {
    if (filter.value === 'all') return debugState.entries
    return debugState.entries.filter((e) => e.kind === filter.value)
})

const errorCount = computed(
    () => debugState.entries.filter((e) => e.kind === 'error').length
)

function toggle() {
    isOpen.value = !isOpen.value
}

function kindColor(kind) {
    return {
        error: '#f56c6c',
        xhr: '#e6a23c',
        api: '#409eff',
        console: '#909399',
        info: '#67c23a',
    }[kind] || '#909399'
}

function detailText(detail) {
    if (!detail) return ''
    return Object.keys(detail)
        .filter((k) => detail[k] != null && detail[k] !== '')
        .map((k) => {
            const v = detail[k]
            return Array.isArray(v)
                ? v.map((x) => `${k}: ${x}`).join('\n')
                : `${k}: ${v}`
        })
        .join('\n')
}

async function copyAll() {
    const text = buildReport()
    try {
        await navigator.clipboard.writeText(text)
        ElMessage.success(`已复制 ${debugState.entries.length} 条日志`)
    } catch {
        // 剪贴板 API 在非安全上下文（http + 非 localhost）下不可用，回退到 textarea
        const ta = document.createElement('textarea')
        ta.value = text
        ta.style.position = 'fixed'
        ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.select()
        try {
            document.execCommand('copy')
            ElMessage.success(`已复制 ${debugState.entries.length} 条日志`)
        } catch {
            ElMessage.error('复制失败，请手动全选')
        }
        document.body.removeChild(ta)
    }
}

function download() {
    const blob = new Blob([buildReport()], { type: 'text/plain;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `media2doc-debug-${Date.now()}.txt`
    a.click()
    URL.revokeObjectURL(a.href)
    ElMessage.success('已下载')
}

function clear() {
    clearEntries()
    ElMessage.success('已清空')
}

async function scrollToBottom() {
    await nextTick()
    const el = listRef.value
    if (el) el.scrollTop = el.scrollHeight
}
</script>

<template>
  <!-- 悬浮球 -->
  <div class="dbg-fab" :class="{ 'dbg-fab--open': isOpen }" @click="toggle" :title="isOpen ? '收起调试面板' : '打开调试面板'">
    <span class="dbg-fab__icon">{{ isOpen ? '×' : '🐞' }}</span>
    <span v-if="!isOpen && errorCount > 0" class="dbg-fab__badge">{{ errorCount > 99 ? '99+' : errorCount }}</span>
  </div>

  <!-- 面板 -->
  <div v-if="isOpen" class="dbg-panel">
    <div class="dbg-head">
      <span class="dbg-title">调试日志</span>
      <span class="dbg-count">{{ debugState.entries.length }} 条</span>
    </div>

    <div class="dbg-actions">
      <button class="dbg-btn dbg-btn--primary" @click="copyAll">复制全部</button>
      <button class="dbg-btn" @click="download">下载</button>
      <button class="dbg-btn" @click="clear">清空</button>
      <button class="dbg-btn" @click="scrollToBottom">到底部</button>
    </div>

    <div class="dbg-filters">
      <span
        v-for="f in filters"
        :key="f.key"
        class="dbg-chip"
        :class="{ 'dbg-chip--on': filter === f.key }"
        @click="filter = f.key"
      >{{ f.label }}</span>
    </div>

    <div ref="listRef" class="dbg-list">
      <div v-if="filtered.length === 0" class="dbg-empty">暂无日志</div>
      <div v-for="e in filtered" :key="e.id" class="dbg-item">
        <div class="dbg-item__head">
          <span class="dbg-time">{{ e.time }}</span>
          <span class="dbg-kind" :style="{ background: kindColor(e.kind) }">{{ e.kind }}</span>
        </div>
        <div class="dbg-item__title">{{ e.title }}</div>
        <pre v-if="detailText(e.detail)" class="dbg-item__detail">{{ detailText(e.detail) }}</pre>
      </div>
    </div>

    <div class="dbg-foot">
      拦截 fetch / XHR / console / 全局错误
    </div>
  </div>
</template>

<style scoped>
.dbg-fab {
  position: fixed;
  right: 22px;
  bottom: 22px;
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: #303133;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  z-index: 3000;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.28);
  user-select: none;
  transition: transform 0.15s, background 0.15s;
}
.dbg-fab:hover { transform: scale(1.08); background: #409eff; }
.dbg-fab--open { background: #f56c6c; }
.dbg-fab__icon { font-size: 20px; line-height: 1; }
.dbg-fab__badge {
  position: absolute;
  top: -4px;
  right: -4px;
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  border-radius: 9px;
  background: #f56c6c;
  color: #fff;
  font-size: 11px;
  line-height: 18px;
  text-align: center;
  font-weight: 600;
}

.dbg-panel {
  position: fixed;
  right: 22px;
  bottom: 82px;
  width: 480px;
  max-width: calc(100vw - 44px);
  height: 560px;
  max-height: calc(100vh - 120px);
  background: #1e1e1e;
  color: #d4d4d4;
  border-radius: 10px;
  box-shadow: 0 10px 34px rgba(0, 0, 0, 0.42);
  z-index: 2999;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  font-size: 12px;
}

.dbg-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  background: #252526;
  border-bottom: 1px solid #333;
}
.dbg-title { font-weight: 600; font-size: 13px; }
.dbg-count { color: #888; font-size: 11px; }

.dbg-actions {
  display: flex;
  gap: 6px;
  padding: 8px 12px;
  border-bottom: 1px solid #333;
}
.dbg-btn {
  flex: 1;
  padding: 5px 0;
  background: #333;
  color: #d4d4d4;
  border: 1px solid #444;
  border-radius: 4px;
  cursor: pointer;
  font-size: 11px;
}
.dbg-btn:hover { background: #3d3d3d; }
.dbg-btn--primary { background: #0e639c; border-color: #1177bb; color: #fff; }
.dbg-btn--primary:hover { background: #1177bb; }

.dbg-filters {
  display: flex;
  gap: 6px;
  padding: 8px 12px;
  border-bottom: 1px solid #333;
  flex-wrap: wrap;
}
.dbg-chip {
  padding: 2px 9px;
  border-radius: 10px;
  background: #333;
  cursor: pointer;
  font-size: 11px;
}
.dbg-chip--on { background: #0e639c; color: #fff; }

.dbg-list {
  flex: 1;
  overflow-y: auto;
  padding: 6px 0;
}
.dbg-empty { text-align: center; color: #666; padding: 30px 0; }

.dbg-item {
  padding: 6px 12px;
  border-bottom: 1px solid #2a2a2a;
}
.dbg-item__head { display: flex; align-items: center; gap: 6px; margin-bottom: 2px; }
.dbg-time { color: #6a9955; font-family: Consolas, monospace; font-size: 10px; }
.dbg-kind {
  padding: 0 5px;
  border-radius: 3px;
  color: #fff;
  font-size: 9px;
  text-transform: uppercase;
}
.dbg-item__title { word-break: break-all; line-height: 1.45; }
.dbg-item__detail {
  margin: 4px 0 0;
  padding: 5px 7px;
  background: #141414;
  border-radius: 3px;
  color: #9cdcfe;
  font-family: Consolas, monospace;
  font-size: 10px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 220px;
  overflow-y: auto;
}

.dbg-foot {
  padding: 6px 12px;
  border-top: 1px solid #333;
  color: #666;
  font-size: 10px;
  text-align: center;
}
</style>
