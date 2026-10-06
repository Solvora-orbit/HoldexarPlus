<script setup lang="ts">
/**
 * 捆绑包中心（plus.3）：多站捆绑包的统一入口，顶替原 Steam 捆绑包浏览页
 * （旧视图保留在 views/bundles/Index.vue，脱离路由备回归）。
 *
 * 范围：HB 月包（当月包 + 近一年进包记录，包内游戏对应 Steam，
 * 点击进详情看价格）+ Steam 捆绑包链接导入；其余站源（HB 捆绑包 /
 * Fanatical / 绿巨人）为注册表预留位，接入时在 SOURCES 加一项并补
 * 对应面板即可，页面骨架不变。
 *
 * 进包数据链：后端逐月抓取往期 membership 页打标（KV 按月记账幂等），
 * 本页只做本地读（GET /metadata/hb/history）+ 手动补抓触发与轮询。
 */
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { bundlesApi, metadataApi, type GameListItem, type HbHistoryMonth } from '@/api/client'
import { useRegionsStore } from '@/stores/regions'
import { useI18n, type MessageKey } from '@/locales'
import HlGameCard from '@/components/business/HlGameCard.vue'
import HumbleBundlesPanel from '@/components/business/HumbleBundlesPanel.vue'
import SteamBundlesPanel from '@/components/business/SteamBundlesPanel.vue'
import HlBacktop from '@/components/ui/HlBacktop.vue'
import HlButton from '@/components/ui/HlButton.vue'
import HlDialog from '@/components/ui/HlDialog.vue'
import HlEmpty from '@/components/ui/HlEmpty.vue'
import HlIcon from '@/components/ui/HlIcon.vue'
import HlInput from '@/components/ui/HlInput.vue'
import HlSpinner from '@/components/ui/HlSpinner.vue'
import { message } from '@/components/ui'

type SourceKey = 'hb-monthly' | 'hb-bundles' | 'steam-bundles' | 'fanatical' | 'greenman'

/** 站源注册表：soon=true 的只渲染禁用态占位，接入时改成 false 并挂面板 */
const SOURCES: { key: SourceKey; labelKey: MessageKey; soon?: boolean }[] = [
  { key: 'hb-monthly', labelKey: 'bundleshub.source.hbMonthly' },
  { key: 'hb-bundles', labelKey: 'bundleshub.source.hbBundles' },
  { key: 'steam-bundles', labelKey: 'bundleshub.source.steamBundles' },
  { key: 'fanatical', labelKey: 'bundleshub.source.fanatical', soon: true },
  { key: 'greenman', labelKey: 'bundleshub.source.greenman', soon: true },
]

function isSourceKey(v: unknown): v is SourceKey {
  return typeof v === 'string' && SOURCES.some((s) => s.key === v)
}

const route = useRoute()
const router = useRouter()
const regionsStore = useRegionsStore()
const { t } = useI18n()

/* keep-alive 名：进出中心不丢选中的源与月份（App.vue include 登记） */
defineOptions({ name: 'BundlesHub' })

const activeSource = ref<SourceKey>(isSourceKey(route.query.source) ? route.query.source : 'hb-monthly')

function pickSource(key: SourceKey) {
  if (key === activeSource.value) return
  activeSource.value = key
  syncQuery()
}

/** 可深链状态进 URL（AGENTS 前端交互规范）：刷新/分享停在同一个源 */
function syncQuery() {
  void router.replace({
    query: { ...route.query, source: activeSource.value === 'hb-monthly' ? undefined : activeSource.value },
  })
}

// ─── 进包记录（近一年月份）+ 期游戏 ───
const months = ref<HbHistoryMonth[]>([])
const monthsLoading = ref(true)
const running = ref(false)
const selectedMonth = ref('')
const monthItems = ref<GameListItem[]>([])
const monthTotal = ref(0)
const monthLoading = ref(false)

async function loadMonths() {
  try {
    const res = await metadataApi.hbHistory()
    running.value = res.running
    months.value = res.months
    if (res.running) schedulePoll()
    if (!selectedMonth.value || !res.months.some((m) => m.label === selectedMonth.value)) {
      selectedMonth.value = res.months[0]?.label ?? ''
      if (selectedMonth.value) void loadMonthGames()
    }
    // 进包记录不足一年且没有任务在跑：自动补抓一轮（0.2.0；打包版更新
    // 当天错过 06:40 定点时免干等到次日。后端按 KV 月份记账幂等 + 重入
    // 锁，本页每个实例只触发一次；调度器的启动补跑是更上游的双保险）
    if (!res.running && !autoBackfillTried && res.months.length < 12) {
      autoBackfillTried = true
      void refreshHistory()
    }
  } finally {
    monthsLoading.value = false
  }
}

let autoBackfillTried = false

async function loadMonthGames() {
  if (!selectedMonth.value) return
  monthLoading.value = true
  try {
    const res = await metadataApi.hbHistoryMonth(selectedMonth.value)
    monthItems.value = res.items
    monthTotal.value = res.total
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    monthLoading.value = false
  }
}

function pickMonth(label: string) {
  if (label === selectedMonth.value) return
  selectedMonth.value = label
  void loadMonthGames()
}

/** 手动补抓：后端任务立即返回，running 期间轮询月份清单直到收敛 */
async function refreshHistory() {
  try {
    const r = await metadataApi.refreshHbHistory()
    if (r.ok) {
      running.value = true
      message.info(t('bundleshub.history.started'))
      schedulePoll()
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  }
}

let pollTimer: number | null = null

function schedulePoll() {
  if (pollTimer != null) return
  pollTimer = window.setInterval(async () => {
    try {
      const res = await metadataApi.hbHistory()
      running.value = res.running
      months.value = res.months
      if (!res.running) {
        stopPoll()
        message.success(t('bundleshub.history.done'))
        if (!res.months.some((m) => m.label === selectedMonth.value)) {
          selectedMonth.value = res.months[0]?.label ?? ''
        }
        void loadMonthGames()
      }
    } catch {
      /* 轮询失败下一轮再试（补抓要好几分钟，不必为此报错） */
    }
  }, 4000)
}

function stopPoll() {
  if (pollTimer != null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

onMounted(() => {
  loadMonths().catch((e) =>
    message.error(e instanceof Error ? e.message : String(e)))
  if (running.value) schedulePoll()
})

/* keep-alive 常驻：切走停轮询，切回有任务在跑就续上 */
onActivated(() => {
  if (running.value) schedulePoll()
})
onDeactivated(stopPoll)
onBeforeUnmount(stopPoll)

// ─── Steam 捆绑包导入（原任务页「捆绑包导入」区块并入此处）───
const importOpen = ref(false)
const importText = ref('')
const importing = ref(false)

async function doImportBundle() {
  const text = importText.value.trim()
  if (!text || importing.value) return
  importing.value = true
  try {
    const r = await bundlesApi.importBundle(text)
    message.success(t(r.existed ? 'crawl.bundle.refreshed' : 'crawl.bundle.imported', {
      name: r.name || `#${r.bundleId}`,
    }))
    importText.value = ''
    importOpen.value = false
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    importing.value = false
  }
}

const monthCountLabel = computed(() =>
  selectedMonth.value
    ? t('bundleshub.monthCount', { n: monthTotal.value })
    : '',
)
</script>

<template>
  <section class="bundle-hub">
    <!-- 页头：一句定位 + 导入入口（完整 HB 卡内容在源面板里） -->
    <div class="hub-head">
      <p class="hub-desc">{{ t('bundleshub.desc') }}</p>
      <HlButton size="sm" @click="importOpen = true">
        <HlIcon name="download" :size="14" />
        {{ t('crawl.bundle.import') }}
      </HlButton>
    </div>

    <!-- 站源切换（注册表式；soon 位只做占位） -->
    <div class="hub-sources">
      <button
        v-for="s in SOURCES"
        :key="s.key"
        type="button"
        class="hub-source"
        :class="{ active: activeSource === s.key }"
        :disabled="s.soon"
        @click="pickSource(s.key)"
      >
        {{ t(s.labelKey) }}
        <span v-if="s.soon" class="hub-source__soon">{{ t('bundleshub.source.soon') }}</span>
      </button>
    </div>

    <!-- HB 月包面板 -->
    <template v-if="activeSource === 'hb-monthly'">
      <!-- 进包记录（近一年）：月份 chips 即记录，款数随行 -->
      <div class="hub-record">
        <span class="hub-record__title">{{ t('bundleshub.record.title') }}</span>
        <template v-if="monthsLoading">
          <HlSpinner />
        </template>
        <template v-else-if="months.length">
          <button
            v-for="m in months"
            :key="m.label"
            type="button"
            class="hub-month"
            :class="{ active: selectedMonth === m.label }"
            @click="pickMonth(m.label)"
          >
            {{ m.label }}
            <span class="hub-month__count">{{ m.count }}</span>
          </button>
        </template>
        <span v-if="running" class="hub-running">{{ t('bundleshub.history.refreshing') }}</span>
        <div class="hub-record__spacer"></div>
        <HlButton size="sm" variant="text" :disabled="running" :loading="running" @click="refreshHistory">
          <HlIcon v-if="!running" name="refresh" :size="14" />
          {{ t('bundleshub.history.refresh') }}
        </HlButton>
      </div>

      <!-- 期游戏：与找游戏同款卡片，点击进详情看价格 -->
      <div v-if="monthLoading" class="hub-loading">
        <HlSpinner />
        {{ t('library.loadingMore') }}
      </div>
      <HlEmpty v-else-if="!selectedMonth || monthItems.length === 0" icon="" style="--pane-pad: 40px 24px">
        <p>{{ t('bundleshub.empty') }}</p>
        <HlButton size="sm" :disabled="running" @click="refreshHistory">
          {{ t('bundleshub.history.refresh') }}
        </HlButton>
      </HlEmpty>
      <template v-else>
        <div class="hub-record__meta">
          <span>{{ selectedMonth }} · {{ monthCountLabel }}</span>
        </div>
        <!-- grid-auto-rows 等高拉伸，消除可选区块行差异造成的参差（同榜单页） -->
        <div class="hub-grid">
          <HlGameCard
            v-for="game in monthItems"
            :key="game.appid"
            :game="game"
            layout-mode="grid"
            :enabled-regions="regionsStore.enabledCodes"
          />
        </div>
      </template>
    </template>

    <!-- HB 捆绑包面板（Humble Bundle 商店包：包卡 + 抽屉游戏卡比价） -->
    <HumbleBundlesPanel v-else-if="activeSource === 'hb-bundles'" />

    <!-- Steam 捆绑包展示区（已导入的 bundle/sub；导入入口保留在页头） -->
    <SteamBundlesPanel v-else-if="activeSource === 'steam-bundles'" />

    <!-- 未接入站源：诚实占位 -->
    <HlEmpty v-else icon="" style="--pane-pad: 48px 24px">
      <p>{{ t('bundleshub.soon.desc') }}</p>
    </HlEmpty>

    <!-- Steam 捆绑包导入 -->
    <HlDialog v-model="importOpen" :title="t('bundleshub.import.title')" :width="440">
      <HlInput
        v-model="importText"
        :placeholder="t('bundleshub.import.placeholder')"
        @keydown.enter="doImportBundle"
      />
      <div class="hub-import__actions">
        <HlButton size="sm" variant="primary" :loading="importing" :disabled="importing || !importText.trim()" @click="doImportBundle">
          {{ t('crawl.bundle.import') }}
        </HlButton>
        <HlButton size="sm" variant="text" :disabled="importing" @click="importOpen = false">
          {{ t('common.cancel') }}
        </HlButton>
      </div>
    </HlDialog>

    <HlBacktop target=".view-container" />
  </section>
</template>

<style scoped>
.bundle-hub {
  max-width: 1200px;
  margin: 0 auto;
}
.hub-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}
.hub-desc {
  font-size: 13px;
  color: var(--text-secondary);
}
.hub-sources {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.hub-source {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 16px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--bg-soft);
  color: var(--text-primary);
  cursor: pointer;
  font-size: 13px;
}
.hub-source.active {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-a10);
}
.hub-source:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.hub-source__soon {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--surface-chip-2);
  color: var(--text-dim);
}
.hub-record {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 10px 12px;
  margin-bottom: 12px;
  background: var(--bg-card);
  border: 1px solid var(--border-soft);
  border-radius: 10px;
}
.hub-record__title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-primary);
  flex-shrink: 0;
}
.hub-record__meta {
  font-size: 12.5px;
  color: var(--text-secondary);
  margin-bottom: 8px;
}
.hub-record__spacer {
  flex: 1;
}
.hub-month {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 12px;
}
.hub-month.active {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-a10);
}
.hub-month__count {
  font-size: 10px;
  font-variant-numeric: tabular-nums;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--surface-chip-2);
  color: var(--text-dim);
}
.hub-month.active .hub-month__count {
  background: var(--accent-a15);
  color: var(--accent);
}
.hub-running {
  font-size: 11.5px;
  color: var(--warning);
}
.hub-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 40px 0;
  color: var(--text-secondary);
}
.hub-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 12px;
  grid-auto-rows: 1fr;
}
.hub-grid > :deep(.game-card) {
  height: 100%;
}
.hub-import__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
}
</style>
