<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

import { systemApi, LOGS_STREAM_URL } from '@/api/client'
import { useI18n } from '@/locales'
import { HlButton, HlIcon, message } from '@/components/ui'

/**
 * 运行日志：进入页面先拉环形缓冲最近 300 行，再经 SSE 续播增量。
 * 跟随滚动 = 贴底时新行自动滚到底；用户上翻即暂停跟随，「回到底部」一键恢复。
 * 复制：正文直接拖选后 Ctrl+C（拖选期间新日志不抢视口）；按钮复制全部缓冲。
 */

const MAX_LINES = 1500 // 前端保留上限（超出丢最旧行，避免长驻页面内存增长）
const REPLAY = 300 // 进页回放行数

const { t } = useI18n()

const lines = ref<string[]>([])
const connected = ref(false)
const paused = ref(false)
const logBoxRef = ref<HTMLElement | null>(null)

let source: EventSource | null = null

async function scrollToBottom() {
  await nextTick()
  const el = logBoxRef.value
  if (el) el.scrollTop = el.scrollHeight
}

/** 用户是否正贴着底部（距底 24px 内视为贴底） */
function isAtBottom(): boolean {
  const el = logBoxRef.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < 24
}

/** 日志框内是否存在拖选中的文本（有选区时视口归用户，新行不许抢滚动） */
function hasSelection(): boolean {
  const sel = window.getSelection()
  if (!sel || sel.isCollapsed) return false
  const box = logBoxRef.value
  return !!box && !!sel.anchorNode && box.contains(sel.anchorNode)
}

function push(line: string) {
  lines.value.push(line)
  if (lines.value.length > MAX_LINES) lines.value.splice(0, lines.value.length - MAX_LINES)
  if (!paused.value && !hasSelection()) scrollToBottom()
}

/** 滚动位置驱动跟随：离开底部即暂停，滚回底部自动恢复（scroll 在滚动生效后触发）*/
function onScroll() {
  paused.value = !isAtBottom()
}

/** 「回到底部并跟随」：清除拖选（选区不清会继续压住跟随）→ 滚到底并恢复跟随 */
function resumeFollow() {
  window.getSelection()?.removeAllRanges()
  paused.value = false
  scrollToBottom()
}

/* ── 复制（正文拖选 + Ctrl+C / 按钮复制全部缓冲）── */

const copiedAll = ref(false)

/** 写剪贴板：clipboard API 优先，旧 WebView2 无权限时回落 execCommand */
async function writeClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    /* 权限拒绝/无焦点 → 走回落 */
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    ta.remove()
    return ok
  } catch {
    return false
  }
}

async function copyAll() {
  if (!lines.value.length) return
  const ok = await writeClipboard(lines.value.join('\n'))
  if (!ok) {
    message.error(t('logs.copy.failedHint'))
    return
  }
  copiedAll.value = true
  window.setTimeout(() => (copiedAll.value = false), 1500)
}

async function load() {
  try {
    const res = await systemApi.logs(REPLAY)
    lines.value = res.lines
    scrollToBottom()
  } catch {
    /* 回放失败不阻塞：SSE 增量照常 */
  }
}

function startStream() {
  source = new EventSource(LOGS_STREAM_URL)
  source.onopen = () => {
    connected.value = true
  }
  source.onmessage = (e) => {
    // 后端整行 JSON 编码（含换行的 traceback 也不会破坏 SSE 帧）
    try {
      push(JSON.parse(e.data) as string)
    } catch {
      push(e.data)
    }
  }
  source.onerror = () => {
    connected.value = false // EventSource 自动重连；重连成功 onopen 再次置位
  }
}

onMounted(() => {
  load()
  startStream()
})

onBeforeUnmount(() => {
  source?.close()
  source = null
})

/* ── 行结构人话化（0.3.0）：`时间 [级别] logger: 消息` 拆段呈现，
   模块名给中文标签；缓冲仍是原始行（复制全部/SSE 续播的事实源不变） ── */

const LINE_RE = /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \[(\w+)\] ([\w.]+): ([\s\S]*)$/

/** logger 模块前缀表（最长前缀优先；表外模块原样显示） */
const MOD_PREFIXES = [
  'app.domains.proxypool', 'app.domains.proxies', 'app.domains.pilot', 'app.domains.agent',
  'app.domains.wishlist', 'app.domains.crawl', 'app.domains.games', 'app.domains.rates',
  'app.domains.alerts', 'app.domains.humble', 'app.domains.metadata', 'app.domains.account',
  'app.domains.monitoring', 'app.domains.steam_events', 'app.domains.achievements',
  'app.domains.family', 'app.domains.bills', 'app.domains.redeem', 'app.domains.regions',
  'app.domains.settings', 'app.domains.system', 'app.domains.notifications',
  'app.crawler', 'app.core.scheduler', 'app.core.database', 'app.core.updater',
  'app.core.events', 'app.core.backup', 'app.core.seed_assets', 'app.core.orchestration',
  'app.core.config', 'app.core.logging', 'app.core.keyring', 'app.core.paths',
  'desktop', 'app.main', 'run', 'app',
]

function levelTag(tag: string): 'error' | 'warning' | 'info' {
  if (tag === 'ERROR' || tag === 'CRITICAL') return 'error'
  if (tag === 'WARNING') return 'warning'
  return 'info'
}

function moduleLabel(name: string): string {
  for (const p of MOD_PREFIXES) {
    if (name === p || name.startsWith(p + '.')) {
      // 动态拼 key：i18n 门禁不判动态引用；缺词条回退原始模块名
      const key = `logs.mod.${p}`
      const hit = (t as (k: string) => string)(key)
      return hit === key ? name : hit
    }
  }
  return name
}

interface ParsedLine {
  ts: string
  level: 'error' | 'warning' | 'info'
  mod: string
  msg: string
  raw: string
}

/** parsed：渲染投影。非匹配行（traceback 续行等）继承上一行级别、整行作消息。 */
const parsed = computed<ParsedLine[]>(() => {
  let lastLevel: ParsedLine['level'] = 'info'
  return lines.value.map((line) => {
    const m = LINE_RE.exec(line)
    if (m) {
      lastLevel = levelTag(m[2])
      return { ts: m[1], level: lastLevel, mod: moduleLabel(m[3]), msg: m[4], raw: line }
    }
    return { ts: '', level: lastLevel, mod: '', msg: line, raw: line }
  })
})

const levelCount = computed(() => {
  let error = 0
  let warning = 0
  for (const l of parsed.value) {
    if (l.level === 'error') error += 1
    else if (l.level === 'warning') warning += 1
  }
  return { error, warning }
})
</script>

<template>
  <section class="logs-page">
    <div class="logs-toolbar">
      <div class="logs-toolbar__state">
        <span class="logs-dot" :class="{ on: connected }" />
        <span>{{ t(connected ? 'logs.stream.connected' : 'logs.stream.connecting') }}</span>
        <span class="logs-toolbar__sep">·</span>
        <span>{{ t('logs.count.lines', { n: lines.length }) }}</span>
        <template v-if="levelCount.error > 0 || levelCount.warning > 0">
          <span class="logs-toolbar__sep">·</span>
          <span class="logs-cnt logs-cnt--error">{{
            t('logs.count.errors', { n: levelCount.error })
          }}</span>
          <span class="logs-cnt logs-cnt--warning">{{
            t('logs.count.warnings', { n: levelCount.warning })
          }}</span>
        </template>
      </div>

      <div class="logs-toolbar__actions">
        <HlButton size="sm" :disabled="!paused" @click="resumeFollow">
          <HlIcon name="refresh" />{{ t(paused ? 'logs.action.resume' : 'logs.action.following') }}
        </HlButton>
        <HlButton size="sm" :disabled="!lines.length" @click="copyAll">
          <HlIcon name="copy" />{{
            copiedAll ? t('common.copied') : t('logs.action.copyAll', { n: lines.length })
          }}
        </HlButton>
        <HlButton size="sm" @click="lines = []">
          <HlIcon name="delete" />{{ t('logs.action.clear') }}
        </HlButton>
      </div>
    </div>

    <div ref="logBoxRef" class="logs-box" @scroll.passive="onScroll">
      <div v-if="lines.length === 0" class="logs-empty">
        {{ t('logs.empty') }}
      </div>
      <div
        v-for="(l, i) in parsed"
        :key="i"
        class="logs-line"
        :class="`is-${l.level}`"
        :title="l.raw"
      >
        <span v-if="l.ts" class="logs-line__ts">{{ l.ts }}</span>
        <span v-if="l.mod" class="logs-line__mod">{{ l.mod }}</span>
        <span class="logs-line__msg">{{ l.msg }}</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.logs-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  /* 视口高 - 顶栏 56 - view-container 上下留白约 32 + 工具行自身高度 */
  height: calc(100vh - 56px - 32px);
  padding: 0 4px;
}

.logs-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  flex-shrink: 0;
}

.logs-toolbar__state {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-muted);
}

.logs-toolbar__sep {
  color: var(--text-faint);
}

.logs-cnt--error {
  color: var(--danger);
}

.logs-cnt--warning {
  color: var(--warning);
}

.logs-toolbar__actions {
  display: flex;
  gap: 8px;
}

.logs-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-faint);
  flex-shrink: 0;
}

.logs-dot.on {
  background: var(--success);
  box-shadow: 0 0 6px var(--success-a40);
}

.logs-box {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  background: var(--surface-inset);
  border: 1px solid var(--line-1);
  border-radius: var(--radius);
  padding: 12px 14px;
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.65;
}

.logs-line {
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--text-muted);
  /* 拖选复制：文本选择交还系统原生行为（选中后 Ctrl+C），拖选期间新日志
     不抢滚动（见 push），选完一段不会被滚走 */
  cursor: text;
  user-select: text;
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
}

/* 分段呈现（0.3.0 人话化）：时间弱化、模块名做标签、消息吃剩余宽度 */
.logs-line__ts {
  color: var(--text-dim);
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}
.logs-line__mod {
  flex-shrink: 0;
  padding: 0 6px;
  border-radius: 4px;
  background: var(--surface-inset);
  color: var(--text-secondary);
  font-size: 11px;
}
.logs-line__msg {
  flex: 1 1 auto;
  min-width: 0;
}

.logs-line.is-warning {
  color: var(--warning);
}

.logs-line.is-error {
  color: var(--danger);
}

.logs-empty {
  color: var(--text-faint);
  font-size: 13px;
  padding: 24px 0;
  text-align: center;
}
</style>
