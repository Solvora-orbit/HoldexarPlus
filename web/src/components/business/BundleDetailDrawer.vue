<script setup lang="ts">
/* 捆绑包站源共享详情抽屉（0.4.0）：HB / Fanatical / GMG 三面板共用。
 *
 * 从 HumbleBundlesPanel 抽屉整体抽出（0.2.3 档位选择器 + 0.3.1 解锁角标 +
 * 0.3.2 卡形占位 + 欠账三态行），档位条支持三种语义：
 * - cumulative（HB/GMG）：累进售卖——点档展示「买该档实拿全部」，本档新增
 *   带角标，解锁价 = 首现档价；
 * - tiered（Fanatical 档位包）：档间独立集合——games = 该档全部，解锁价 =
 *   该档价（0.4.0 实测档间零交集）；
 * - pick（Fanatical 选择包）：数量档——games = 全池，解锁价 = 最低档价，
 *   档名「选 N 款」。
 * 价格一律按 props.currency 从原生多币种 priceMap 取值展示（零折算），
 * HB 固定传 'CNY'（priceMap = {CNY: fen}）。
 */
import { computed, ref, watch } from 'vue'

import type { GameListItem, HumblePendingGame, StoreTier } from '@/api/client'
import { formatMoney } from '@/api/regions'
import { APP_SLUG } from '@/appInfo'
import { useRegionsStore } from '@/stores/regions'
import { useI18n } from '@/locales'
import HlGameCard from '@/components/business/HlGameCard.vue'
import { HlDrawer, HlEmpty, HlSpinner } from '@/components/ui'

export interface BundleDrawerTierGame {
  title: string
  appid: number | null
  isNew?: boolean
  unlockPriceMap?: Record<string, number> | null
}

const props = defineProps<{
  open: boolean
  loading: boolean
  title: string
  /** 头部包价展示串（面板按站源/币种格式化后传入；null 显 —） */
  priceDisplay: string | null
  /** 头部销量展示串（HB 专属「已购 N」；其他站不传） */
  soldDisplay?: string | null
  gameCount: number
  period: string
  url: string
  kind: 'cumulative' | 'tiered' | 'pick'
  currency: string
  tiers: StoreTier[]
  items: GameListItem[]
  pending: HumblePendingGame[]
  /** 抽屉宽度记忆键（HB 迁移沿用旧键保住用户已调宽度） */
  storageKey?: string
}>()

const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>()

const openModel = computed({
  get: () => props.open,
  set: (v: boolean) => emit('update:open', v),
})

const { t } = useI18n()
const regionsStore = useRegionsStore()

/* 初始宽取 px 数字串（HlDrawer 的 resizable 初始宽要可 parseInt；超视口由拖拽钳制兜底） */
const drawerWidth = '980px'

/* appid → 卡片；pending → 状态：档位组内非卡片条目回挂 */
const cardByAppid = computed(() => {
  const m = new Map<number, GameListItem>()
  for (const g of props.items) m.set(g.appid, g)
  return m
})
const pendingByTitle = computed(() => {
  const m = new Map<string, HumblePendingGame>()
  for (const p of props.pending) m.set(p.title, p)
  return m
})

/** 非卡片条目状态 → 文案键（三态：关联中/收录中/未找到） */
function humStatusKey(status: HumblePendingGame['status']): string {
  if (status === 'ingesting') return 'bundles.store.ingesting'
  if (status === 'not_found') return 'bundles.store.notFound'
  return 'bundles.store.resolving'
}

/** 档位组里没卡片的游戏行的状态文案键 */
function rowStatusKey(title: string, appid: number | null): string {
  const p = pendingByTitle.value.get(title)
  if (p) return humStatusKey(p.status)
  return appid == null ? 'bundles.store.resolving' : 'bundles.store.ingesting'
}

/* 档位选择器：选中的档按 kind 语义展示；换包/重开回第一档 */
const activeTier = ref(0)
watch(() => [props.open, props.tiers], () => {
  activeTier.value = 0
})

const activeTierData = computed<StoreTier | null>(() => props.tiers[activeTier.value] ?? null)
const activeTierGames = computed<BundleDrawerTierGame[]>(
  () => (activeTierData.value?.games ?? activeTierData.value?.newGames ?? []) as BundleDrawerTierGame[],
)

function tierName(tier: StoreTier, idx: number): string {
  if (props.kind === 'pick') return t('bundles.store.pickTier', { n: tier.quantity ?? 0 })
  return tier.header || t('bundles.store.tier', { i: idx + 1 })
}

function tierMeta(tier: StoreTier): string {
  if (props.kind === 'pick') return t('bundles.store.pickMeta', { n: tier.games?.length ?? 0 })
  if (props.kind === 'tiered') return t('bundles.store.tierCount', { n: tier.games?.length ?? 0 })
  return t('bundles.store.tierCount', { n: tier.count })
}

const tierHint = computed(() => {
  const tier = activeTierData.value
  if (!tier) return ''
  if (props.kind === 'pick') {
    return t('bundles.store.pickHint', { n: tier.games?.length ?? 0 })
  }
  if (props.kind === 'tiered') return t('bundles.store.tieredHint', { n: tier.games?.length ?? 0 })
  return t('bundles.store.tierHint', { n: tier.count, m: tier.newCount ?? 0 })
})

function tierPrice(tier: StoreTier): string {
  const minor = tier.priceMap?.[props.currency]
  return minor != null ? formatMoney(minor, props.currency) : '—'
}

function unlockText(g: BundleDrawerTierGame): string | null {
  const minor = g.unlockPriceMap?.[props.currency]
  return minor != null ? t('bundles.store.unlockAt', { price: formatMoney(minor, props.currency) }) : null
}

/** 不属于任何档位的 pending 行（旧数据/边缘）兜底组 */
const tierTitles = computed(() => {
  const s = new Set<string>()
  for (const tier of props.tiers) {
    for (const g of tier.games ?? tier.newGames ?? []) s.add(g.title)
  }
  return s
})
const loosePending = computed(() =>
  props.pending.filter((p) => !tierTitles.value.has(p.title)),
)
</script>

<template>
  <HlDrawer
    v-model="openModel"
    :title="title"
    :width="drawerWidth"
    resizable
    :min-width="560"
    :storage-key="storageKey ?? `${APP_SLUG}.bundle.drawer-w`"
  >
    <div class="bd-drawer__head">
      <span v-if="priceDisplay" class="bd-drawer__price">{{ priceDisplay }}</span>
      <span v-if="soldDisplay" class="bd-drawer__sold">{{ soldDisplay }}</span>
      <span class="bd-drawer__count">
        {{ t('bundles.store.gameCount', { n: gameCount }) }}
      </span>
      <span v-if="period" class="bd-drawer__period">{{ period }}</span>
      <a
        v-if="url"
        class="bd-drawer__official"
        :href="url"
        target="_blank"
        rel="noopener noreferrer"
      >{{ t('bundles.store.official') }} ↗</a>
    </div>
    <div v-if="loading" class="bd-loading">
      <HlSpinner /> {{ t('library.loadingMore') }}
    </div>
    <template v-else>
      <!-- 档位选择器：点档切换，按 kind 语义展示 -->
      <section v-if="tiers.length" class="bd-tiers">
        <div class="bd-tiers__bar" role="tablist" :aria-label="t('bundles.store.tierBar')">
          <button
            v-for="(t0, ti) in tiers"
            :key="t0.id"
            type="button"
            role="tab"
            class="bd-tier-btn"
            :class="{ 'is-active': ti === activeTier }"
            :aria-selected="ti === activeTier"
            @click="activeTier = ti"
          >
            <span class="bd-tier-btn__name">{{ tierName(t0, ti) }}</span>
            <span class="bd-tier-btn__price">{{ tierPrice(t0) }}</span>
            <span class="bd-tier-btn__meta">{{ tierMeta(t0) }}</span>
          </button>
        </div>
        <p v-if="tierHint" class="bd-tiers__hint">{{ tierHint }}</p>
        <div v-if="activeTierGames.length === 0" class="bd-tier__empty">
          {{ t('bundles.store.tierEmpty') }}
        </div>
        <div v-else class="bd-tier__body">
          <div v-for="g in activeTierGames" :key="g.title" class="bd-tier-cell">
            <span v-if="g.isNew" class="bd-tier-cell__new">
              {{ t('bundles.store.tierNewBadge') }}
            </span>
            <!-- 解锁门槛角标：该游解锁最低价（按 kind 语义给值），不点选也辨价位 -->
            <span v-if="unlockText(g)" class="bd-tier-cell__unlock">
              {{ unlockText(g) }}
            </span>
            <HlGameCard
              v-if="g.appid != null && cardByAppid.has(g.appid)"
              :game="cardByAppid.get(g.appid)!"
              layout-mode="grid"
              :enabled-regions="regionsStore.enabledCodes"
            />
            <!-- 没卡片的游戏：卡形占位（封面位+标题+状态），与真卡同格同形 -->
            <div v-else class="bd-pending__card">
              <div class="bd-pending__card-cover">
                <span class="bd-pending__card-glyph" aria-hidden="true">{{ g.title.slice(0, 1) }}</span>
              </div>
              <div class="bd-pending__card-body">
                <span class="bd-pending__card-name">{{ g.title }}</span>
                <span class="bd-pending__card-status">{{ t(rowStatusKey(g.title, g.appid)) }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 无档位数据（旧包未重抓）：回退平铺卡片 -->
      <div v-if="!tiers.length && items.length" class="bd-grid">
        <HlGameCard
          v-for="game in items"
          :key="game.appid"
          :game="game"
          layout-mode="grid"
          :enabled-regions="regionsStore.enabledCodes"
        />
      </div>
      <!-- 不属于任何档位的欠账条目（兜底组，保证外层计数与抽屉一致） -->
      <ul v-if="loosePending.length" class="bd-pending">
        <li v-for="p in loosePending" :key="p.title" class="bd-pending__row">
          <span class="bd-pending__dot" aria-hidden="true"></span>
          <span class="bd-pending__name">{{ p.title }}</span>
          <span class="bd-pending__status">{{ t(humStatusKey(p.status)) }}</span>
        </li>
      </ul>
      <HlEmpty v-if="!items.length && !loosePending.length && !tiers.length" icon="" size="sm">
        <p>{{ t('bundles.store.detailEmpty') }}</p>
      </HlEmpty>
    </template>
  </HlDrawer>
</template>

<style scoped>
.bd-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 32px 0;
  color: var(--text-secondary);
}
.bd-drawer__head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.bd-drawer__price {
  font-size: 16px;
  font-weight: 700;
  color: var(--success);
  font-variant-numeric: tabular-nums;
}
.bd-drawer__sold,
.bd-drawer__count,
.bd-drawer__period {
  font-size: 12px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}
.bd-drawer__official {
  margin-left: auto;
  font-size: 12px;
  color: var(--accent);
  text-decoration: none;
}
.bd-drawer__official:hover { text-decoration: underline; }
/* 档位选择条（0.2.3 参照 HB 官网档位切换；0.4.0 扩三语义） */
.bd-tiers {
  margin-bottom: 14px;
}
.bd-tiers__bar {
  display: flex;
  align-items: stretch;
  gap: 8px;
  flex-wrap: wrap;
  margin: 2px 0 8px;
}
.bd-tier-btn {
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
.bd-tier-btn:hover {
  border-color: var(--accent-a50);
}
.bd-tier-btn.is-active {
  border-color: var(--accent-a50);
  background: var(--surface-chip-2);
  box-shadow: inset 0 0 0 1px var(--accent-a50);
}
.bd-tier-btn__name {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
}
.bd-tier-btn.is-active .bd-tier-btn__name {
  color: var(--accent);
}
.bd-tier-btn__price {
  font-size: 14px;
  font-weight: 700;
  color: var(--success);
  font-variant-numeric: tabular-nums;
}
.bd-tier-btn__meta {
  font-size: 10.5px;
  color: var(--text-dim);
  font-variant-numeric: tabular-nums;
}
.bd-tiers__hint {
  margin: 0 0 10px;
  font-size: 11.5px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}
.bd-tier__empty {
  font-size: 12px;
  color: var(--text-dim);
  padding: 6px 0;
}
.bd-tier__body {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
  grid-auto-rows: 1fr;
}
.bd-tier-cell {
  position: relative;
  min-width: 0;
}
.bd-tier-cell > :deep(.game-card),
.bd-tier-cell > .bd-pending__row {
  height: 100%;
}
.bd-tier-cell__new {
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
/* 解锁门槛角标：卡左下角标档位价，弱化不遮封面主体 */
.bd-tier-cell__unlock {
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
/* 卡形占位：档位格里未入目录的条目与真卡同形同高 */
.bd-pending__card {
  display: flex;
  flex-direction: column;
  border: 1px dashed var(--border-soft);
  border-radius: var(--radius);
  background: var(--bg-card);
  overflow: hidden;
  height: 100%;
}
.bd-pending__card-cover {
  aspect-ratio: 16 / 9;
  display: grid;
  place-items: center;
  background: var(--surface-inset);
}
.bd-pending__card-glyph {
  font-size: 30px;
  font-weight: 700;
  color: var(--text-dim);
}
.bd-pending__card-body {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 9px 12px 11px;
}
.bd-pending__card-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bd-pending__card-status {
  font-size: 11px;
  color: var(--text-dim);
}
.bd-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
  grid-auto-rows: 1fr;
}
.bd-grid > :deep(.game-card) {
  height: 100%;
}
.bd-pending {
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
.bd-pending__row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: var(--text-secondary);
  min-width: 0;
}
.bd-pending__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--warning);
  flex-shrink: 0;
}
.bd-pending__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bd-pending__status {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-dim);
}
</style>
