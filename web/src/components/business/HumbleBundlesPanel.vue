<script setup lang="ts">
/* HB 捆绑包面板（捆绑包中心「HB 捆绑包」源，0.2.0 / 0.2.1 交互补全）。
   数据源 humbleApi（后端每日抓取 zh.humblebundle.com/games 列表 + 在售包
   详情；纯本地读）。包卡 → 抽屉内游戏卡网格（与月包面板同款 HlGameCard，
   点进详情看各区价格——「方便查看比价」）；抽屉可拖左缘调宽并记忆。
   0.2.1：包卡显示档期；列表默认只出前 8 个（按结束时间升序=快下架在前），
   「展开全部」收放；每日扫出的新包带 NEW 角标，点开包即已读；抽屉条目数
   与外层一致（收录中的条目以轻量行列出，状态注明）。
   0.2.3：档位改为选择器交互（参照 HB 官网）——点档切换，网格展示买该档
   实际拿到的全部游戏（累进语义，本档新增带角标）。 */
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue'

import { humbleApi, type HumbleBundleItem, type HumblePendingGame, type HumbleTier } from '@/api/client'
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
/* 收录轮（0.2.3）：ingestPending>0 出「收录中 N」触发卡；点击立即分批首爬 */
const ingestRunning = ref(false)
const ingestPending = ref(0)

async function load() {
  try {
    const res = await humbleApi.bundles()
    bundles.value = res.bundles
    running.value = res.running
    ingestRunning.value = res.ingestRunning ?? false
    ingestPending.value = res.ingestPending ?? 0
    if (res.running || res.ingestRunning) schedulePoll()
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    loading.value = false
  }
}

async function ingestNow() {
  if (ingestRunning.value) return
  try {
    const r = await humbleApi.ingestNow()
    if (r.ok && (r.started || r.running)) {
      ingestRunning.value = true
      message.info(t('bundles.humble.ingestStarted'))
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
      const res = await humbleApi.bundles()
      const wasRefresh = running.value
      const wasIngest = ingestRunning.value
      running.value = res.running
      ingestRunning.value = res.ingestRunning ?? false
      ingestPending.value = res.ingestPending ?? 0
      bundles.value = res.bundles
      if (!res.running && !res.ingestRunning) {
        stopPoll()
        if (wasIngest) message.success(t('bundles.humble.ingestDone'))
        else if (wasRefresh) message.success(t('bundles.humble.refreshDone'))
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

/* 倒计时三档（0.2.2 全包显示）：≤3 天紧迫红 / ≤14 天警示 / 其余弱化 */
function urgencyClass(endAt: string | null): string {
  const d = daysLeft(endAt)
  if (d === null) return ''
  if (d <= 3) return 'is-hot'
  if (d <= 14) return 'is-warn'
  return 'is-cool'
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
/* 价格档位（0.2.2）：非空则抽屉按档分组；旧数据空数组回退平铺 */
const detailTiers = ref<HumbleTier[]>([])

/* appid → 卡片；title → pending 状态：档位组内非卡片条目回挂 */
const cardByAppid = computed(() => {
  const m = new Map<number, GameListItem>()
  for (const g of detailItems.value) m.set(g.appid, g)
  return m
})
const pendingByTitle = computed(() => {
  const m = new Map<string, HumblePendingGame>()
  for (const p of detailPending.value) m.set(p.title, p)
  return m
})
/** 非卡片条目状态 → 文案键（三态：收录中/关联中/未找到，0.3.2） */
function humStatusKey(status: HumblePendingGame['status']): string {
  if (status === 'ingesting') return 'bundles.humble.ingesting'
  if (status === 'not_found') return 'bundles.humble.notFound'
  return 'bundles.humble.resolving'
}

/** 档位组里没卡片的游戏行的状态文案键 */
function rowStatusKey(title: string, appid: number | null): string {
  const p = pendingByTitle.value.get(title)
  if (p) return humStatusKey(p.status)
  return appid == null ? 'bundles.humble.resolving' : 'bundles.humble.ingesting'
}
/* 档位选择器（0.2.3，参照 HB 官网档位切换交互）：选中的档展示「买那档
   实际拿到的全部游戏」（累进清单），本档新增带角标；旧数据无 games 时
   回退本档新增口径 */
const activeTier = ref(0)
const activeTierData = computed<HumbleTier | null>(() => detailTiers.value[activeTier.value] ?? null)
const activeTierGames = computed(() => activeTierData.value?.games ?? activeTierData.value?.newGames ?? [])

/** 不属于任何档位的 pending 行（旧数据/边缘：条目在但档位清单没它）兜底组 */
const tierTitles = computed(() => {
  const s = new Set<string>()
  for (const t of detailTiers.value) {
    for (const g of t.games ?? t.newGames) s.add(g.title)
  }
  return s
})
const loosePending = computed(() =>
  detailPending.value.filter((p) => !tierTitles.value.has(p.title)),
)

async function openBundle(b: HumbleBundleItem) {
  detail.value = b
  detailItems.value = []
  detailTotal.value = 0
  detailPending.value = []
  detailTiers.value = []
  activeTier.value = 0
  open.value = true
  detailLoading.value = true
  try {
    const res = await humbleApi.bundleDetail(b.slug)
    detail.value = { ...b, ...res }
    detailItems.value = res.items
    detailTotal.value = res.total
    detailPending.value = res.pending ?? []
    detailTiers.value = res.tiers ?? []
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
      <div class="hb-panel__tools">
        <!-- 收录中 N（0.2.3）：有欠账才出现；点击立即触发一轮分批首爬 -->
        <button
          v-if="ingestPending > 0"
          type="button"
          class="hb-ingest-chip"
          :class="{ 'is-busy': ingestRunning }"
          :disabled="ingestRunning"
          :aria-label="t('bundles.humble.ingestCount', { n: ingestPending })"
          @click="ingestNow"
        >
          <span
            class="hb-ingest-chip__dot"
            :class="{ 'is-pulse': ingestRunning }"
            aria-hidden="true"
          ></span>
          {{ t('bundles.humble.ingestCount', { n: ingestPending }) }}
        </button>
        <HlButton size="sm" variant="text" :disabled="running" :loading="running" @click="refresh">
          <HlIcon v-if="!running" name="refresh" :size="14" />
          {{ t('bundles.humble.refresh') }}
        </HlButton>
      </div>
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
            <span v-if="daysLeft(b.endAt) !== null" class="hb-card__ending" :class="urgencyClass(b.endAt)">
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
        <!-- 档位选择器（0.2.3，参照 HB 官网）：点档切换，网格展示「买该档
             实际拿到的全部游戏」（累进）；本档新增带角标 -->
        <section v-if="detailTiers.length" class="hb-tiers">
          <div class="hb-tiers__bar" role="tablist" :aria-label="t('bundles.humble.tierBar')">
            <button
              v-for="(t0, ti) in detailTiers"
              :key="t0.id"
              type="button"
              role="tab"
              class="hb-tier-btn"
              :class="{ 'is-active': ti === activeTier }"
              :aria-selected="ti === activeTier"
              @click="activeTier = ti"
            >
              <span class="hb-tier-btn__name">{{ t('bundles.humble.tier', { i: ti + 1 }) }}</span>
              <span class="hb-tier-btn__price">
                {{ t0.priceCnyFen != null ? formatCnyFen(t0.priceCnyFen) : '—' }}
              </span>
              <span class="hb-tier-btn__meta">{{ t('bundles.humble.tierCount', { n: t0.count }) }}</span>
            </button>
          </div>
          <p v-if="activeTierData" class="hb-tiers__hint">
            {{ t('bundles.humble.tierHint', { n: activeTierData.count, m: activeTierData.newCount }) }}
          </p>
          <div v-if="activeTierGames.length === 0" class="hb-tier__empty">
            {{ t('bundles.humble.tierEmpty') }}
          </div>
          <div v-else class="hb-tier__body">
            <div v-for="g in activeTierGames" :key="g.title" class="hb-tier-cell">
              <span v-if="g.isNew" class="hb-tier-cell__new">
                {{ t('bundles.humble.tierNewBadge') }}
              </span>
              <!-- 解锁门槛角标（0.3.1）：该游首次出现的档位价，不点选也辨价位 -->
              <span v-if="g.unlockPriceCnyFen != null" class="hb-tier-cell__unlock">
                {{ t('bundles.humble.unlockAt', { price: formatCnyFen(g.unlockPriceCnyFen) }) }}
              </span>
              <HlGameCard
                v-if="g.appid != null && cardByAppid.has(g.appid)"
                :game="cardByAppid.get(g.appid)!"
                layout-mode="grid"
                :enabled-regions="regionsStore.enabledCodes"
              />
              <!-- 没卡片的游戏：卡形占位（封面位+标题+状态），与真卡同格同形，
                   单款档位不再塌成一条信息（0.3.2） -->
              <div v-else class="hb-pending__card">
                <div class="hb-pending__card-cover">
                  <span class="hb-pending__card-glyph" aria-hidden="true">{{ g.title.slice(0, 1) }}</span>
                </div>
                <div class="hb-pending__card-body">
                  <span class="hb-pending__card-name">{{ g.title }}</span>
                  <span class="hb-pending__card-status">{{ t(rowStatusKey(g.title, g.appid)) }}</span>
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- 无档位数据（旧包未重抓）：回退平铺卡片 -->
        <div v-if="!detailTiers.length && detailItems.length" class="hb-drawer__grid">
          <HlGameCard
            v-for="game in detailItems"
            :key="game.appid"
            :game="game"
            layout-mode="grid"
            :enabled-regions="regionsStore.enabledCodes"
          />
        </div>
        <!-- 不属于任何档位的欠账条目（兜底组，保证外层计数与抽屉一致） -->
        <ul v-if="loosePending.length" class="hb-pending">
          <li v-for="p in loosePending" :key="p.title" class="hb-pending__row">
            <span class="hb-pending__dot" aria-hidden="true"></span>
            <span class="hb-pending__name">{{ p.title }}</span>
            <span class="hb-pending__status">{{ t(humStatusKey(p.status)) }}</span>
          </li>
        </ul>
        <HlEmpty v-if="!detailItems.length && !loosePending.length && !detailTiers.length" icon="" size="sm">
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
.hb-panel__tools {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
/* 收录中 N 触发芯片（0.2.3）：数字卡 hover 亲和态口径（cursor+边框/底色反馈） */
.hb-ingest-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border: 1px solid var(--border-soft);
  border-radius: 999px;
  background: var(--surface-inset);
  color: var(--text-secondary);
  font-size: 11.5px;
  font-variant-numeric: tabular-nums;
  cursor: pointer;
  transition: border-color var(--transition), background var(--transition), color var(--transition);
}
.hb-ingest-chip:hover {
  border-color: var(--accent-a50);
  background: var(--surface-chip-2);
  color: var(--text-primary);
}
.hb-ingest-chip.is-busy {
  cursor: progress;
  color: var(--accent);
}
.hb-ingest-chip__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--warning);
  flex-shrink: 0;
}
.hb-ingest-chip__dot.is-pulse {
  animation: hb-chip-pulse 1.1s ease-in-out infinite;
}
@keyframes hb-chip-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
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
  font-size: 10.5px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
/* 倒计时三档（0.2.2 全包显示）：紧迫红 / 警示 / 弱化 */
.hb-card__ending.is-hot { background: var(--danger); color: var(--ink-on-fill); }
.hb-card__ending.is-warn { background: var(--warning); color: var(--ink-on-fill); }
.hb-card__ending.is-cool {
  background: var(--surface-chip-2);
  color: var(--text-secondary);
  font-weight: 500;
}
/* 档位选择条（0.2.3，参照 HB 官网档位切换） */
.hb-tiers {
  margin-bottom: 14px;
}
.hb-tiers__bar {
  display: flex;
  align-items: stretch;
  gap: 8px;
  flex-wrap: wrap;
  margin: 2px 0 8px;
}
.hb-tier-btn {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 8px 14px;
  border: 1px solid var(--border-soft);
  border-radius: 10px;
  background: var(--bg-card);
  color: var(--text-primary);
  cursor: pointer;
  transition: border-color var(--transition), background var(--transition), box-shadow var(--transition);
}
.hb-tier-btn:hover {
  border-color: var(--accent-a50);
}
.hb-tier-btn.is-active {
  border-color: var(--accent-a50);
  background: var(--surface-chip-2);
  box-shadow: inset 0 0 0 1px var(--accent-a50);
}
.hb-tier-btn__name {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
}
.hb-tier-btn.is-active .hb-tier-btn__name {
  color: var(--accent);
}
.hb-tier-btn__price {
  font-size: 14px;
  font-weight: 700;
  color: var(--success);
  font-variant-numeric: tabular-nums;
}
.hb-tier-btn__meta {
  font-size: 10.5px;
  color: var(--text-dim);
  font-variant-numeric: tabular-nums;
}
.hb-tiers__hint {
  margin: 0 0 10px;
  font-size: 11.5px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}
.hb-tier__empty {
  font-size: 12px;
  color: var(--text-dim);
  padding: 6px 0;
}
.hb-tier__body {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
  grid-auto-rows: 1fr;
}
.hb-tier-cell {
  position: relative;
  min-width: 0;
}
.hb-tier-cell > :deep(.game-card),
.hb-tier-cell > .hb-pending__row {
  height: 100%;
}
.hb-tier-cell__new {
  position: absolute;
  top: 6px;
  right: 6px;
  z-index: 1;
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--accent);
  color: var(--ink-on-fill);
  font-size: 10px;
  font-weight: 700;
  pointer-events: none;
}
/* 解锁门槛角标（0.3.1）：卡左下角标档位价，弱化不遮封面主体 */
.hb-tier-cell__unlock {
  position: absolute;
  bottom: 6px;
  left: 6px;
  z-index: 1;
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--surface-inset);
  border: 1px solid var(--border-soft);
  color: var(--text-secondary);
  font-size: 10px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  pointer-events: none;
}
/* 卡形占位（0.3.2）：档位格里未入目录的条目与真卡同形同高 */
.hb-pending__card {
  display: flex;
  flex-direction: column;
  border: 1px dashed var(--border-soft);
  border-radius: var(--radius);
  background: var(--bg-card);
  overflow: hidden;
  height: 100%;
}
.hb-pending__card-cover {
  aspect-ratio: 16 / 9;
  display: grid;
  place-items: center;
  background: var(--surface-inset);
}
.hb-pending__card-glyph {
  font-size: 30px;
  font-weight: 700;
  color: var(--text-dim);
}
.hb-pending__card-body {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 9px 12px 11px;
}
.hb-pending__card-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hb-pending__card-status {
  font-size: 11px;
  color: var(--text-dim);
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
