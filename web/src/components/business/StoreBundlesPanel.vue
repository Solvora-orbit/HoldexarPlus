<script setup lang="ts">
/* Fanatical / GMG 共用站源面板（0.4.0）：与 HB 面板同构的交互
 * （包卡 → 抽屉档位条 + 游戏卡网格 + 三态欠账 + 收录中芯片）。
 *
 * 与 HB 的差异都在 props：站源 API、档位语义（fanatical 按包 kind 分
 * tiered 独立集/pick 数量档；gmg 恒累进）、描述文案键。
 * 货币切换器（用户约定）：面板头小切换控件，列出该站数据实有币种，
 * 默认 CNY 优先 → USD；展示站点原生价、零折算，选择记忆到 localStorage。
 */
import { computed, ref, watch } from 'vue'

import type { StoreBundleItem } from '@/api/client'
import { formatMoney, pickDefaultCurrency } from '@/api/regions'
import { APP_SLUG } from '@/appInfo'
import { useBundleSource, type BundleSourceApi } from '@/composables/useBundleSource'
import { useI18n } from '@/locales'
import { HlButton, HlEmpty, HlIcon, HlImg } from '@/components/ui'
import BundleDetailDrawer from '@/components/business/BundleDetailDrawer.vue'

const props = defineProps<{
  /** 站源键（货币记忆键用）：fanatical / greenman */
  sourceKey: string
  api: BundleSourceApi
  /** 面板描述文案键 */
  descKey: string
  /** 未带 kind 字段的包按此档位语义渲染（fanatical=tiered / gmg=cumulative） */
  defaultKind: 'tiered' | 'cumulative'
}>()

const { t } = useI18n()

const src = useBundleSource(props.api, 'bundles.store')
const {
  bundles, loading, running, ingestRunning, ingestPending,
  expanded, shown, hiddenCount,
  open, detailLoading, detail, detailItems, detailPending, detailTiers,
  refresh, ingestNow, openBundle, period, daysLeft, isLongRunning, urgencyClass,
} = src

// ─── 货币切换（站点原生多币种、零折算）───
const CURRENCY_ORDER = ['CNY', 'USD', 'EUR', 'GBP', 'JPY', 'CAD', 'AUD', 'RUB']
const storageKey = `${APP_SLUG}.store.currency.${props.sourceKey}`

const currency = ref(localStorage.getItem(storageKey) || '')

/** 数据实有币种并集（按展示顺序排） */
const currencyOptions = computed(() => {
  const present = new Set<string>()
  for (const b of bundles.value) {
    const pm = (b as StoreBundleItem).priceMap
    if (pm) for (const c of Object.keys(pm)) present.add(c)
  }
  const ordered = CURRENCY_ORDER.filter((c) => present.has(c))
  for (const c of present) if (!ordered.includes(c)) ordered.push(c)
  return ordered
})

/* 默认币种：记忆 → CNY 优先 → USD → 首个可得；数据币种消失时回落 */
watch(
  [currencyOptions, currency],
  () => {
    const opts = currencyOptions.value
    if (!opts.length) return
    if (!currency.value || !opts.includes(currency.value)) {
      const saved = localStorage.getItem(storageKey) || ''
      currency.value = saved && opts.includes(saved)
        ? saved
        : pickDefaultCurrency(
          Object.fromEntries(opts.map((c) => [c, 1])),
        )
      localStorage.setItem(storageKey, currency.value)
    }
  },
  { immediate: true },
)

function pickCurrency(c: string) {
  currency.value = c
  localStorage.setItem(storageKey, c)
}

function money(b: StoreBundleItem): string {
  const minor = b.priceMap?.[currency.value]
  return minor != null ? formatMoney(minor, currency.value) : '—'
}

const drawerPrice = computed(() => {
  const d = detail.value as (StoreBundleItem & Record<string, unknown>) | null
  const pm = d?.priceMap
  const minor = pm ? pm[currency.value] : undefined
  return minor != null ? formatMoney(minor, currency.value) : null
})
const drawerKind = computed(() => {
  const d = detail.value as (StoreBundleItem & { kind?: string }) | null
  if (d?.kind === 'pick') return 'pick' as const
  if (d?.kind === 'tiered') return 'tiered' as const
  return props.defaultKind
})
const drawerPeriod = computed(() => (detail.value ? period(detail.value) : ''))
</script>

<template>
  <div class="sp-panel">
    <div class="sp-panel__head">
      <p class="sp-panel__desc">{{ t(descKey) }}</p>
      <div class="sp-panel__tools">
        <!-- 货币切换：站点原生价零折算，切换即时生效 -->
        <div
          v-if="currencyOptions.length > 1"
          class="sp-ccy"
          role="radiogroup"
          :aria-label="t('bundles.store.currencyToggle')"
        >
          <button
            v-for="c in currencyOptions"
            :key="c"
            type="button"
            role="radio"
            class="sp-ccy__btn"
            :class="{ 'is-active': c === currency }"
            :aria-checked="c === currency"
            @click="pickCurrency(c)"
          >{{ c }}</button>
        </div>
        <button
          v-if="ingestPending > 0"
          type="button"
          class="sp-ingest-chip"
          :class="{ 'is-busy': ingestRunning }"
          :disabled="ingestRunning"
          :aria-label="t('bundles.store.ingestCount', { n: ingestPending })"
          @click="ingestNow"
        >
          <span
            class="sp-ingest-chip__dot"
            :class="{ 'is-pulse': ingestRunning }"
            aria-hidden="true"
          ></span>
          {{ t('bundles.store.ingestCount', { n: ingestPending }) }}
        </button>
        <HlButton size="sm" variant="text" :disabled="running" :loading="running" @click="refresh">
          <HlIcon v-if="!running" name="refresh" :size="14" />
          {{ t('bundles.store.refresh') }}
        </HlButton>
      </div>
    </div>

    <div v-if="loading" class="sp-panel__loading">
      <HlSpinner /> {{ t('library.loadingMore') }}
    </div>
    <HlEmpty v-else-if="bundles.length === 0" icon="" style="--pane-pad: 40px 24px">
      <p>{{ t('bundles.store.empty') }}</p>
      <HlButton size="sm" :disabled="running" @click="refresh">{{ t('bundles.store.refresh') }}</HlButton>
    </HlEmpty>
    <template v-else>
      <div class="sp-grid">
        <button
          v-for="b in shown"
          :key="b.slug"
          type="button"
          class="sp-card"
          @click="openBundle(b)"
        >
          <div class="sp-card__cover">
            <HlImg :src="b.image" :alt="b.name" loading="lazy">
              <template #fallback>
                <div class="sp-card__cover-fallback">{{ b.name.slice(0, 1) }}</div>
              </template>
            </HlImg>
            <span v-if="b.isNew" class="sp-card__new">NEW</span>
            <span
              v-if="daysLeft(b.endAt) !== null && !isLongRunning(b.endAt)"
              class="sp-card__ending"
              :class="urgencyClass(b.endAt)"
            >
              {{ t('bundles.store.ending', { d: daysLeft(b.endAt) }) }}
            </span>
          </div>
          <div class="sp-card__body">
            <div class="sp-card__name">{{ b.name }}</div>
            <div class="sp-card__meta">
              <span class="sp-card__price">{{ money(b as StoreBundleItem) }}</span>
              <span v-if="(b as StoreBundleItem).kind === 'pick'" class="sp-card__kind">
                {{ t('bundles.store.pickBadge') }}
              </span>
              <span class="sp-card__count">
                {{ t('bundles.store.gameCount', { n: b.gameCount }) }}
              </span>
            </div>
            <div v-if="period(b)" class="sp-card__period">
              <HlIcon name="calendar" :size="11" />
              {{ t('bundles.store.period') }} {{ period(b) }}
            </div>
          </div>
        </button>
      </div>
      <div v-if="hiddenCount > 0 || expanded" class="sp-more">
        <HlButton size="sm" variant="text" @click="expanded = !expanded">
          {{ expanded
            ? t('bundles.store.collapse')
            : t('bundles.store.expand', { n: hiddenCount }) }}
        </HlButton>
      </div>
    </template>

    <!-- 包详情抽屉：共享组件（kind 随包：tiered 独立集 / pick 数量档） -->
    <BundleDetailDrawer
      v-model:open="open"
      :loading="detailLoading"
      :title="detail?.name ?? ''"
      :price-display="drawerPrice"
      :game-count="detail?.gameCount ?? 0"
      :period="drawerPeriod"
      :url="detail?.url ?? ''"
      :kind="drawerKind"
      :currency="currency"
      :tiers="detailTiers"
      :items="detailItems"
      :pending="detailPending"
    />
  </div>
</template>

<style scoped>
.sp-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 4px;
}
.sp-panel__desc {
  font-size: 12.5px;
  color: var(--text-secondary);
  margin: 0;
}
.sp-panel__tools {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
/* 货币切换器：紧凑分段控件，与 ingest 芯片同视觉族 */
.sp-ccy {
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--border-soft);
  border-radius: 999px;
  background: var(--surface-inset);
  overflow: hidden;
}
.sp-ccy__btn {
  padding: 3px 9px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.3px;
  cursor: pointer;
  transition: background var(--transition), color var(--transition);
}
.sp-ccy__btn + .sp-ccy__btn {
  border-left: 1px solid var(--border-soft);
}
.sp-ccy__btn:hover {
  color: var(--text-primary);
}
.sp-ccy__btn.is-active {
  background: var(--accent-a50);
  color: var(--text-primary);
}
.sp-ingest-chip {
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
.sp-ingest-chip:hover {
  border-color: var(--accent-a50);
  background: var(--surface-chip-2);
  color: var(--text-primary);
}
.sp-ingest-chip.is-busy {
  cursor: progress;
  color: var(--accent);
}
.sp-ingest-chip__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--warning);
  flex-shrink: 0;
}
.sp-ingest-chip__dot.is-pulse {
  animation: sp-chip-pulse 1.1s ease-in-out infinite;
}
@keyframes sp-chip-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
.sp-panel__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 32px 0;
  color: var(--text-secondary);
}
.sp-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
  margin-top: 10px;
}
.sp-card {
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
.sp-card:hover {
  border-color: var(--accent-a50);
  transform: translateY(-2px);
  box-shadow: var(--shadow-lg);
}
.sp-card__cover {
  position: relative;
  aspect-ratio: 16 / 9;
  background: var(--surface-inset);
}
.sp-card__cover :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.sp-card__cover-fallback {
  width: 100%;
  height: 100%;
  display: grid;
  place-items: center;
  font-size: 28px;
  font-weight: 700;
  color: var(--text-dim);
}
.sp-card__new {
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
.sp-card__ending {
  position: absolute;
  top: 8px;
  right: 8px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 10.5px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.sp-card__ending.is-hot { background: var(--danger); color: var(--ink-on-fill); }
.sp-card__ending.is-warn { background: var(--warning); color: var(--ink-on-fill); }
.sp-card__ending.is-cool {
  background: var(--surface-chip-2);
  color: var(--text-secondary);
  font-weight: 500;
}
.sp-card__body {
  padding: 10px 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.sp-card__name {
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.sp-card__meta {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.sp-card__price {
  font-size: 14px;
  font-weight: 700;
  color: var(--success);
  font-variant-numeric: tabular-nums;
}
/* 选择包角标：与 NEW 角标同族的弱化胶囊 */
.sp-card__kind {
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--surface-chip-2);
  color: var(--text-secondary);
  font-size: 10px;
  font-weight: 600;
}
.sp-card__count {
  margin-left: auto;
  font-size: 11.5px;
  color: var(--text-secondary);
  flex-shrink: 0;
}
.sp-card__period {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 10.5px;
  color: var(--text-dim);
  font-variant-numeric: tabular-nums;
}
.sp-more {
  display: flex;
  justify-content: center;
  padding: 12px 0 2px;
}
</style>
