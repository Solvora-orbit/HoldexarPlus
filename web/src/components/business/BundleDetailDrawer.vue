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
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { bundlesApi, type BundleDetail, type GameListItem, type HumblePendingGame, type PackMember, type StoreTier } from '@/api/client'
import { formatCnyFen, formatMoney } from '@/api/regions'
import { APP_SLUG } from '@/appInfo'
import { useRegionsStore } from '@/stores/regions'
import { useI18n } from '@/locales'
import HlGameCard from '@/components/business/HlGameCard.vue'
import SteamBundleDrawer from '@/components/business/SteamBundleDrawer.vue'
import { HlButton, HlDialog, HlDrawer, HlEmpty, HlImg, HlSpinner } from '@/components/ui'

export interface BundleDrawerTierGame {
  title: string
  appid: number | null
  bundleId?: number | null
  memberAppids?: PackMember[] | null
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
  /** 单包重抓进行中（0.5.1；按钮禁用态） */
  resetting?: boolean
  /** 站点合辑成员卡片集（0.6.0，Fanatical；成员行封面/价格查此） */
  memberItems?: GameListItem[]
}>()

const emit = defineEmits<{ (e: 'update:open', v: boolean): void; (e: 'reset'): void }>()

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
/* 成员卡查找图（0.6.0）：items ∪ memberItems（成员 appid 不在顶层条目里） */
const memberCardByAppid = computed(() => {
  const m = new Map<number, GameListItem>()
  for (const g of props.items) m.set(g.appid, g)
  for (const g of props.memberItems ?? []) if (!m.has(g.appid)) m.set(g.appid, g)
  return m
})

/** 非卡片条目状态 → 文案键（五态：关联中/收录中/未找到/Steam 捆绑包/站点合辑） */
function humStatusKey(status: HumblePendingGame['status']): string {
  if (status === 'ingesting') return 'bundles.store.ingesting'
  if (status === 'not_found') return 'bundles.store.notFound'
  if (status === 'bundle') return 'bundles.store.bundleTag'
  if (status === 'pack') return 'bundles.store.packTag'
  return 'bundles.store.resolving'
}

/** 档位组里没卡片的游戏行的状态文案键 */
function rowStatusKey(title: string, appid: number | null): string {
  const p = pendingByTitle.value.get(title)
  if (p) return humStatusKey(p.status)
  return appid == null ? 'bundles.store.resolving' : 'bundles.store.ingesting'
}

// ── Steam 捆绑包卡（0.6.0）：点击弹右侧二级详情抽屉（bundleState 详情
//    缓存：卡片 meta 与抽屉共用一份，重复打开零请求）──
const bundleState = reactive<{
  details: Map<number, BundleDetail>
  loading: Set<number>
}>({ details: new Map(), loading: new Set() })

const steamOpen = ref(false)
const steamBundleId = ref<number | null>(null)
const steamBundleDetail = computed<BundleDetail | null>(() =>
  (steamBundleId.value != null ? bundleState.details.get(steamBundleId.value) ?? null : null))
const steamBundleLoading = computed(
  () => steamBundleId.value != null && bundleState.loading.has(steamBundleId.value))

async function openSteamBundle(bundleId: number) {
  steamBundleId.value = bundleId
  steamOpen.value = true
  if (bundleState.details.has(bundleId) || bundleState.loading.has(bundleId)) return
  bundleState.loading.add(bundleId)
  try {
    const d = await bundlesApi.detail(String(bundleId))
    bundleState.details.set(bundleId, d)
  } catch {
    /* 拉取失败收起（下次点击重试）；卡片本体不受影响 */
    if (steamBundleId.value === bundleId) steamOpen.value = false
  } finally {
    bundleState.loading.delete(bundleId)
  }
}

function bundleName(bundleId: number, fallback: string): string {
  return bundleState.details.get(bundleId)?.name ?? fallback
}

/* 游戏详情跳转（0.6.0）：站点合辑成员行直达（SteamBundlesPanel .sb-game 先例）*/
const router = useRouter()

function goMember(appid: number) {
  router.push(`/game/${appid}`)
}

// ── 站点合辑卡（0.5.1）：合辑 SKU 站点明示的成员清单，展开即看（零请求）──
const packState = reactive<{ expanded: Set<string> }>({ expanded: new Set() })

/* 单包重抓确认（0.6.0）：HlDialog 层；确认后发事件并自动收起 */
const resetAsk = ref(false)

function onResetConfirm() {
  resetAsk.value = false
  emit('reset')
}

function togglePack(title: string) {
  if (packState.expanded.has(title)) {
    packState.expanded.delete(title)
    return
  }
  packState.expanded.add(title)
}

function packMemberPrice(m: PackMember): string | null {
  if (m.appid == null || !memberCardByAppid.value.has(m.appid)) return null
  return formatCnyFen(memberCardByAppid.value.get(m.appid)!.basePriceFen)
}

function packMemberCover(m: PackMember): string | null {
  if (m.appid == null || !memberCardByAppid.value.has(m.appid)) return null
  return memberCardByAppid.value.get(m.appid)!.headerImage || null
}

/** 合辑卡免点击封面（0.6.0）：首个已入目录成员的卡图，无需展开即出图 */
function packCover(members: PackMember[]): string | null {
  for (const m of members) {
    const cover = packMemberCover(m)
    if (cover) return cover
  }
  return null
}

/* 档位选择器：选中的档按 kind 语义展示；换包/重开回第一档并收二级抽屉 */
const activeTier = ref(0)
watch(() => [props.open, props.tiers], () => {
  activeTier.value = 0
  steamOpen.value = false
  steamBundleId.value = null
  packState.expanded.clear()
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
      <!-- 单包删数据 + 重抓（0.5.1/0.6.0）：HlDialog 确认（popconfirm 在抽屉
           overflow 里会被裁掉——0.6.0 实修「点了没反应」） -->
      <HlButton size="sm" variant="text" :loading="resetting" @click="resetAsk = true">
        {{ t('bundles.store.resetBtn') }}
      </HlButton>
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
            <!-- Steam 捆绑包卡（0.6.0）：条目按 bundle 命中，点击弹右侧详情抽屉 -->
            <div v-else-if="g.bundleId != null" class="bd-bundle" @click="openSteamBundle(g.bundleId)">
              <div class="bd-bundle__cover">
                <HlImg
                  :src="bundleState.details.get(g.bundleId)?.headerImage || ''"
                  :alt="bundleName(g.bundleId, g.title)"
                  loading="lazy"
                >
                  <template #fallback>
                    <span class="bd-bundle__glyph" aria-hidden="true">{{ bundleName(g.bundleId, g.title).slice(0, 1) }}</span>
                  </template>
                </HlImg>
              </div>
              <div class="bd-bundle__body">
                <span class="bd-bundle__tag">{{ t('bundles.store.bundleTag') }}</span>
                <span class="bd-bundle__name">{{ bundleName(g.bundleId, g.title) }}</span>
                <span v-if="bundleState.details.get(g.bundleId)" class="bd-bundle__meta">
                  {{ t('bundles.store.bundleMeta', {
                    n: bundleState.details.get(g.bundleId)!.appIds.length,
                    price: formatCnyFen(bundleState.details.get(g.bundleId)!.cnCnyFen),
                  }) }}
                </span>
                <span v-else class="bd-bundle__meta">{{ t('bundles.store.bundleExpand') }}</span>
              </div>
              <div v-if="bundleState.loading.has(g.bundleId)" class="bd-bundle__loading">
                <HlSpinner />
              </div>
            </div>
            <!-- 站点合辑卡（0.6.0）：免点击出封面（首个已收录成员卡图）；
                 成员行可点直达游戏详情，未关联成员如实标注不可点 -->
            <div v-else-if="g.memberAppids?.length" class="bd-bundle" @click="togglePack(g.title)">
              <div class="bd-bundle__cover">
                <HlImg v-if="packCover(g.memberAppids)" :src="packCover(g.memberAppids)!" :alt="g.title" loading="lazy">
                  <template #fallback>
                    <span class="bd-bundle__glyph" aria-hidden="true">{{ g.title.slice(0, 1) }}</span>
                  </template>
                </HlImg>
                <span v-else class="bd-bundle__glyph" aria-hidden="true">{{ g.title.slice(0, 1) }}</span>
              </div>
              <div class="bd-bundle__body">
                <span class="bd-bundle__tag">{{ t('bundles.store.packTag') }}</span>
                <span class="bd-bundle__name">{{ g.title }}</span>
                <span class="bd-bundle__meta">
                  {{ packState.expanded.has(g.title)
                    ? t('bundles.store.packMeta', { n: g.memberAppids.length })
                    : t('bundles.store.packExpand', { n: g.memberAppids.length }) }}
                </span>
              </div>
              <div v-if="packState.expanded.has(g.title)" class="bd-bundle__games" @click.stop>
                <div
                  v-for="m in g.memberAppids"
                  :key="m.title"
                  class="bd-bundle__game"
                  :class="{ 'is-link': m.appid != null }"
                  :role="m.appid != null ? 'link' : undefined"
                  :tabindex="m.appid != null ? 0 : undefined"
                  @click="m.appid != null && goMember(m.appid)"
                  @keydown.enter="m.appid != null && goMember(m.appid)"
                >
                  <HlImg v-if="packMemberCover(m)" :src="packMemberCover(m)!" :alt="m.title" loading="lazy">
                    <template #fallback>
                      <span class="bd-bundle__game-glyph" aria-hidden="true">{{ m.title.slice(0, 1) }}</span>
                    </template>
                  </HlImg>
                  <span v-else class="bd-bundle__game-glyph" aria-hidden="true">{{ m.title.slice(0, 1) }}</span>
                  <span class="bd-bundle__game-name">{{ m.title }}</span>
                  <span class="bd-bundle__game-price">
                    {{ packMemberPrice(m) ?? t('bundles.store.packMemberUnlinked') }}
                  </span>
                </div>
              </div>
            </div>
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

      <!-- 单包重抓确认（0.6.0）：弹窗层渲染，不受抽屉 overflow 裁剪 -->
      <HlDialog v-model="resetAsk" :title="t('bundles.store.resetBtn')" :width="440">
        <p class="bd-reset__tip">{{ t('bundles.store.resetConfirm') }}</p>
        <template #footer>
          <HlButton variant="text" size="sm" @click="resetAsk = false">
            {{ t('common.cancel') }}
          </HlButton>
          <HlButton variant="primary" size="sm" @click="onResetConfirm">
            {{ t('bundles.store.resetConfirmBtn') }}
          </HlButton>
        </template>
      </HlDialog>

      <!-- Steam 捆绑包详情（0.6.0）：非模态二级抽屉叠加，详情来自 bundleState 缓存 -->
      <SteamBundleDrawer
        v-model="steamOpen"
        :detail="steamBundleDetail"
        :loading="steamBundleLoading"
        :fallback-title="steamBundleId != null ? bundleName(steamBundleId, '') : ''"
      />
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
/* Steam 捆绑包卡（0.5.0）：bundleId 条目的卡形态，点击内嵌展开包内比价 */
.bd-bundle {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--accent-a50);
  border-radius: var(--radius);
  background: var(--bg-card);
  overflow: hidden;
  height: 100%;
  cursor: pointer;
  transition: box-shadow var(--transition), border-color var(--transition);
}
.bd-bundle:hover {
  box-shadow: var(--shadow-lg);
}
.bd-bundle__cover {
  position: relative;
  aspect-ratio: 16 / 9;
  background: var(--surface-inset);
}
.bd-bundle__cover :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.bd-bundle__glyph {
  font-size: 30px;
  font-weight: 700;
  color: var(--text-dim);
}
.bd-bundle__body {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 9px 12px 11px;
}
.bd-bundle__tag {
  align-self: flex-start;
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--accent);
  color: var(--ink-on-fill);
  font-size: 10px;
  font-weight: 700;
}
.bd-bundle__name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.bd-bundle__meta {
  font-size: 11px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}
.bd-bundle__loading {
  display: grid;
  place-items: center;
  padding: 14px 0;
}
/* 内嵌包内游戏行（点开后）：与 SteamBundlesPanel 抽屉行同信息密度 */
.bd-bundle__games {
  border-top: 1px dashed var(--border-soft);
  display: flex;
  flex-direction: column;
  max-height: 260px;
  overflow-y: auto;
}
.bd-bundle__game {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  min-width: 0;
}
/* 成员行直达游戏详情（0.6.0）：可点行 hover 亲和态（交互规范） */
.bd-bundle__game.is-link {
  cursor: pointer;
}
.bd-bundle__game.is-link:hover {
  background: var(--surface-inset);
}
.bd-bundle__game.is-link:hover .bd-bundle__game-name {
  color: var(--accent);
}
.bd-bundle__game + .bd-bundle__game {
  border-top: 1px solid var(--surface-inset);
}
.bd-bundle__game :deep(img),
.bd-bundle__game :deep(.bd-bundle__game-glyph) {
  width: 52px;
  height: 24px;
  object-fit: cover;
  flex-shrink: 0;
  border-radius: 3px;
  display: grid;
  place-items: center;
  background: var(--surface-inset);
  font-size: 12px;
  font-weight: 700;
  color: var(--text-dim);
}
.bd-bundle__game-name {
  font-size: 12px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.bd-bundle__game-price {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--success);
  font-variant-numeric: tabular-nums;
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
.bd-reset__tip {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-secondary);
}
</style>
