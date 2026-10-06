<script setup lang="ts">
/* HB 捆绑包面板（捆绑包中心「HB 捆绑包」源，0.2.0 / 0.2.1 交互补全）。
   数据源 humbleApi（后端每日抓取 zh.humblebundle.com/games 列表 + 在售包
   详情；纯本地读）。包卡 → 抽屉内游戏卡网格（与月包面板同款 HlGameCard，
   点击进详情看各区价格——「方便查看比价」）；抽屉可拖左缘调宽并记忆。
   0.2.1：包卡显示档期；列表默认只出前 8 个（按结束时间升序=快下架在前），
   「展开全部」收放；每日扫出的新包带 NEW 角标，点开包即已读；抽屉条目数
   与外层一致（收录中的条目以轻量行列出，状态注明）。 */
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue'

import { humbleApi, type HumbleBundleItem, type HumblePendingGame } from '@/api/client'
import { formatCnyFen } from '@/api/regions'
import { APP_SLUG } from '@/appInfo'
import { useRegionsStore } from '@/stores/regions'
import { useI18n } from '@/locales'
import HlGameCard from '@/components/business/HlGameCard.vue'
import { HlButton, HlDrawer, HlEmpty, HlIcon, HlImg, HlSpinner, message } from '@/components/ui'
import type { GameListItem } from '@/api/client'

const { t } = useI18n()
const regionsStore = useRegionsStore()

const bundles = ref<HumbleBundleItem[]>([])
const loading = ref(true)
const running = ref(false)

async function load() {
  try {
    const res = await humbleApi.bundles()
    bundles.value = res.bundles
    running.value = res.running
    if (res.running) schedulePoll()
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    loading.value = false
  }
}

let pollTimer: number | null = null

function schedulePoll() {
  if (pollTimer != null) return
  pollTimer = window.setInterval(async () => {
    try {
      const res = await humbleApi.bundles()
      running.value = res.running
      bundles.value = res.bundles
      if (!res.running) {
        stopPoll()
        message.success(t('bundles.humble.refreshDone'))
      }
    } catch {
      /* 轮询失败等下一轮 */
    }
  }, 5000)
}

function stopPoll() {
  if (pollTimer != null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function refresh() {
  try {
    const r = await humbleApi.refresh()
    if (r.ok) {
      running.value = true
      message.info(t('bundles.humble.refreshStarted'))
      schedulePoll()
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  }
}

onMounted(() => void load())
onActivated(() => {
  void load()
})
onDeactivated(stopPoll)
onBeforeUnmount(stopPoll)

// ─── 列表收放：默认前 8（0.2.1 用户约定），其余「展开全部」 ───
const PREVIEW = 8
const expanded = ref(false)
const shown = computed(() => (expanded.value ? bundles.value : bundles.value.slice(0, PREVIEW)))
const hiddenCount = computed(() => Math.max(0, bundles.value.length - PREVIEW))

/* 档期文案：start ~ end（日期短格式）；end 为空 = 长期在售 */
function period(b: HumbleBundleItem): string {
  const f = (iso: string) => iso.slice(5, 10).replace('-', '.')
  if (b.startAt && b.endAt) return `${f(b.startAt)} ~ ${f(b.endAt)}`
  if (b.endAt) return `~ ${f(b.endAt)}`
  if (b.startAt) return `${f(b.startAt)} ~`
  return ''
}

function daysLeft(endAt: string | null): number | null {
  if (!endAt) return null
  const ms = new Date(endAt).getTime() - Date.now()
  return ms > 0 ? Math.max(1, Math.ceil(ms / 86400000)) : 0
}

// ─── 包详情抽屉（可拖宽，宽度记忆；点开 = 已读清 NEW）───
/* 初始宽取 px 数字串（HlDrawer 的 resizable 初始宽要可 parseInt；超视口由拖拽钳制兜底） */
const drawerWidth = '980px'
const open = ref(false)
const detailLoading = ref(false)
const detail = ref<HumbleBundleItem | null>(null)
const detailItems = ref<GameListItem[]>([])
const detailTotal = ref(0)
const detailPending = ref<HumblePendingGame[]>([])

async function openBundle(b: HumbleBundleItem) {
  detail.value = b
  detailItems.value = []
  detailTotal.value = 0
  detailPending.value = []
  open.value = true
  detailLoading.value = true
  try {
    const res = await humbleApi.bundleDetail(b.slug)
    detail.value = { ...b, ...res }
    detailItems.value = res.items
    detailTotal.value = res.total
    detailPending.value = res.pending ?? []
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    detailLoading.value = false
  }
  /* 点开即已读：本地先清徽章（即时反馈），再向后端记账 */
  if (b.isNew) {
    b.isNew = false
    try { await humbleApi.seen(b.slug) } catch { /* 已读失败不扰：下次点开重试 */ }
  }
}
</script>

<template>
  <div class="hb-panel">
    <div class="hb-panel__head">
      <p class="hb-panel__desc">{{ t('bundles.humble.desc') }}</p>
      <HlButton size="sm" variant="text" :disabled="running" :loading="running" @click="refresh">
        <HlIcon v-if="!running" name="refresh" :size="14" />
        {{ t('bundles.humble.refresh') }}
      </HlButton>
    </div>

    <div v-if="loading" class="hb-panel__loading">
      <HlSpinner /> {{ t('library.loadingMore') }}
    </div>
    <HlEmpty v-else-if="bundles.length === 0" icon="" style="--pane-pad: 40px 24px">
      <p>{{ t('bundles.humble.empty') }}</p>
      <HlButton size="sm" :disabled="running" @click="refresh">{{ t('bundles.humble.refresh') }}</HlButton>
    </HlEmpty>
    <template v-else>
      <div class="hb-grid">
        <button
          v-for="b in shown"
          :key="b.slug"
          type="button"
          class="hb-card"
          @click="openBundle(b)"
        >
          <div class="hb-card__cover">
            <HlImg :src="b.image" :alt="b.name" loading="lazy">
              <template #fallback>
                <div class="hb-card__cover-fallback">{{ b.name.slice(0, 1) }}</div>
              </template>
            </HlImg>
            <span v-if="b.isNew" class="hb-card__new">NEW</span>
            <span v-if="daysLeft(b.endAt) !== null && daysLeft(b.endAt)! <= 7" class="hb-card__ending">
              {{ t('bundles.humble.ending', { d: daysLeft(b.endAt) }) }}
            </span>
          </div>
          <div class="hb-card__body">
            <div class="hb-card__name">{{ b.name }}</div>
            <div class="hb-card__meta">
              <span v-if="b.priceCnyFen !== null" class="hb-card__price">{{ formatCnyFen(b.priceCnyFen) }}</span>
              <span v-else class="hb-card__price na">—</span>
              <span class="hb-card__count">{{ t('bundles.humble.gameCount', { n: b.gameCount }) }}</span>
            </div>
            <div v-if="period(b)" class="hb-card__period">
              <HlIcon name="calendar" :size="11" />
              {{ t('bundles.humble.period') }} {{ period(b) }}
            </div>
          </div>
        </button>
      </div>
      <div v-if="hiddenCount > 0 || expanded" class="hb-more">
        <HlButton size="sm" variant="text" @click="expanded = !expanded">
          {{ expanded
            ? t('bundles.humble.collapse')
            : t('bundles.humble.expand', { n: hiddenCount }) }}
        </HlButton>
      </div>
    </template>

    <!-- 包详情抽屉：拖左缘调宽（宽度记忆）；游戏卡网格 + 收录中条目行 -->
    <HlDrawer
      v-model="open"
      :title="detail?.name ?? ''"
      :width="drawerWidth"
      resizable
      :min-width="560"
      :storage-key="`${APP_SLUG}.humble.drawer-w`"
    >
      <div class="hb-drawer__head">
        <span v-if="detail?.priceCnyFen != null" class="hb-drawer__price">{{ formatCnyFen(detail.priceCnyFen) }}</span>
        <span class="hb-drawer__count">
          {{ t('bundles.humble.gameCount', { n: detail?.gameCount ?? 0 }) }}
        </span>
        <span v-if="detail && period(detail)" class="hb-drawer__period">{{ period(detail) }}</span>
        <a
          v-if="detail?.url"
          class="hb-drawer__official"
          :href="detail.url"
          target="_blank"
          rel="noopener noreferrer"
        >{{ t('bundles.humble.official') }} ↗</a>
      </div>
      <div v-if="detailLoading" class="hb-panel__loading">
        <HlSpinner /> {{ t('library.loadingMore') }}
      </div>
      <template v-else>
        <div v-if="detailItems.length" class="hb-drawer__grid">
          <HlGameCard
            v-for="game in detailItems"
            :key="game.appid"
            :game="game"
            layout-mode="grid"
            :enabled-regions="regionsStore.enabledCodes"
          />
        </div>
        <!-- 收录中条目：与卡片合计 = 外层计数（0.2.1 一致性） -->
        <ul v-if="detailPending.length" class="hb-pending">
          <li v-for="p in detailPending" :key="p.title" class="hb-pending__row">
            <span class="hb-pending__dot" aria-hidden="true"></span>
            <span class="hb-pending__name">{{ p.title }}</span>
            <span class="hb-pending__status">
              {{ t(p.status === 'ingesting' ? 'bundles.humble.ingesting' : 'bundles.humble.resolving') }}
            </span>
          </li>
        </ul>
        <HlEmpty v-if="!detailItems.length && !detailPending.length" icon="" size="sm">
          <p>{{ t('bundles.humble.detailEmpty') }}</p>
        </HlEmpty>
      </template>
    </HlDrawer>
  </div>
</template>

<style scoped>
.hb-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 4px;
}
.hb-panel__desc {
  font-size: 12.5px;
  color: var(--text-secondary);
  margin: 0;
}
.hb-panel__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 32px 0;
  color: var(--text-secondary);
}
.hb-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
  margin-top: 10px;
}
.hb-card {
  position: relative;
  display: flex;
  flex-direction: column;
  padding: 0;
  border: 1px solid var(--border-soft);
  border-radius: var(--radius);
  background: var(--bg-card);
  color: var(--text-primary);
  cursor: pointer;
  text-align: left;
  overflow: hidden;
  transition: border-color var(--transition), transform var(--transition), box-shadow var(--transition);
}
.hb-card:hover {
  border-color: var(--accent-a50);
  transform: translateY(-2px);
  box-shadow: var(--shadow-lg);
}
.hb-card__cover {
  position: relative;
  aspect-ratio: 16 / 9;
  background: var(--surface-inset);
}
.hb-card__cover :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.hb-card__cover-fallback {
  width: 100%;
  height: 100%;
  display: grid;
  place-items: center;
  font-size: 28px;
  font-weight: 700;
  color: var(--text-dim);
}
.hb-card__new {
  position: absolute;
  top: 8px;
  left: 8px;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--danger);
  color: var(--ink-on-fill);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.5px;
}
.hb-card__ending {
  position: absolute;
  top: 8px;
  right: 8px;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--danger);
  color: var(--ink-on-fill);
  font-size: 10.5px;
  font-weight: 700;
}
.hb-card__body {
  padding: 10px 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.hb-card__name {
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.hb-card__meta {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.hb-card__price {
  font-size: 14px;
  font-weight: 700;
  color: var(--success);
  font-variant-numeric: tabular-nums;
}
.hb-card__price.na { color: var(--text-dim); }
.hb-card__count {
  font-size: 11.5px;
  color: var(--text-secondary);
}
.hb-card__period {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 10.5px;
  color: var(--text-dim);
  font-variant-numeric: tabular-nums;
}
.hb-more {
  display: flex;
  justify-content: center;
  padding: 12px 0 2px;
}
.hb-drawer__head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.hb-drawer__price {
  font-size: 16px;
  font-weight: 700;
  color: var(--success);
  font-variant-numeric: tabular-nums;
}
.hb-drawer__count,
.hb-drawer__period {
  font-size: 12px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}
.hb-drawer__official {
  margin-left: auto;
  font-size: 12px;
  color: var(--accent);
  text-decoration: none;
}
.hb-drawer__official:hover { text-decoration: underline; }
.hb-drawer__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
  grid-auto-rows: 1fr;
}
.hb-drawer__grid > :deep(.game-card) {
  height: 100%;
}
.hb-pending {
  list-style: none;
  margin: 10px 0 0;
  padding: 10px 12px;
  border: 1px dashed var(--border-soft);
  border-radius: var(--radius);
  background: var(--surface-inset);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.hb-pending__row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: var(--text-secondary);
  min-width: 0;
}
.hb-pending__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--warning);
  flex-shrink: 0;
}
.hb-pending__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hb-pending__status {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-dim);
}
</style>
