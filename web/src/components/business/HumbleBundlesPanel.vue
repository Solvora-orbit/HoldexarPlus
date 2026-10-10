<script setup lang="ts">
/* HB 捆绑包面板（捆绑包中心「HB 捆绑包」源）。
   0.4.0 起抽屉/档位条/欠账行整体迁共享组件 BundleDetailDrawer（与
   Fanatical/GMG 三站同构），本文件只留列表卡与 HB 专属载荷适配：
   - 档位 HumbleTier（priceCnyFen 累进）→ StoreTier 统一形态（priceMap）
   - 销量「已购 N」（0.4.0：列表页 bundles_sold / 详情页 num_purchases）
   交互约定不变：包卡 → 抽屉内档位条 + 游戏卡网格（点档切换累进语义），
   列表默认前 8，NEW 角标点开即已读，收录中 N 触发芯片。 */
import { computed } from 'vue'

import { humbleApi, type HumbleBundleItem, type HumbleTier, type StoreTier } from '@/api/client'
import { formatCnyFen } from '@/api/regions'
import { APP_SLUG } from '@/appInfo'
import { useBundleSource } from '@/composables/useBundleSource'
import { useI18n } from '@/locales'
import { HlButton, HlEmpty, HlIcon, HlImg, message } from '@/components/ui'
import BundleDetailDrawer from '@/components/business/BundleDetailDrawer.vue'
import type { GameListItem } from '@/api/client'

const { t } = useI18n()

/* HB 载荷 → 统一 StoreTier：priceCnyFen → {CNY: fen}，解锁价同形（累进语义，
   抽屉按 cumulative 渲染「买该档实拿全部 + 本档新增角标」不变） */
function mapTiers(payload: Record<string, unknown>): StoreTier[] {
  const tiers = (payload.tiers as HumbleTier[] | undefined) ?? []
  return tiers.map((t0) => ({
    id: t0.id,
    priceMap: t0.priceCnyFen != null ? { CNY: t0.priceCnyFen } : null,
    isInitial: t0.isInitial,
    newGames: (t0.newGames ?? []).map((g) => ({
      title: g.title,
      appid: g.appid,
      isNew: g.isNew,
      unlockPriceMap: g.unlockPriceCnyFen != null ? { CNY: g.unlockPriceCnyFen } : null,
    })),
    newCount: t0.newCount,
    count: t0.count,
    games: (t0.games ?? []).map((g) => ({
      title: g.title,
      appid: g.appid,
      isNew: g.isNew,
      unlockPriceMap: g.unlockPriceCnyFen != null ? { CNY: g.unlockPriceCnyFen } : null,
    })),
  }))
}

const src = useBundleSource(humbleApi, 'bundles.store', { mapTiers })
const {
  bundles, loading, running, ingestRunning, ingestPending,
  expanded, shown, hiddenCount,
  open, detailLoading, detail, detailItems, detailPending, detailTiers,
  refresh, ingestNow, openBundle, period, daysLeft, urgencyClass,
} = src

/** 卡/抽屉头销量（已购 N，千分位；null = 未抓到过不显示） */
function soldText(b: HumbleBundleItem): string | null {
  if (b.soldCount == null) return null
  return t('bundles.humble.soldCount', { n: b.soldCount.toLocaleString('en-US') })
}

const drawerPrice = computed(() => {
  const d = detail.value as (HumbleBundleItem & { priceCnyFen?: number | null }) | null
  return d?.priceCnyFen != null ? formatCnyFen(d.priceCnyFen) : null
})
const drawerSold = computed(() => {
  const d = detail.value as (HumbleBundleItem & { soldCount?: number | null }) | null
  return d ? soldText(d) : null
})
const drawerPeriod = computed(() => (detail.value ? period(detail.value) : ''))
</script>

<template>
  <div class="hb-panel">
    <div class="hb-panel__head">
      <p class="hb-panel__desc">{{ t('bundles.humble.desc') }}</p>
      <div class="hb-panel__tools">
        <!-- 收录中 N：有欠账才出现；点击立即触发一轮分批首爬 -->
        <button
          v-if="ingestPending > 0"
          type="button"
          class="hb-ingest-chip"
          :class="{ 'is-busy': ingestRunning }"
          :disabled="ingestRunning"
          :aria-label="t('bundles.store.ingestCount', { n: ingestPending })"
          @click="ingestNow"
        >
          <span
            class="hb-ingest-chip__dot"
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

    <div v-if="loading" class="hb-panel__loading">
      <HlSpinner /> {{ t('library.loadingMore') }}
    </div>
    <HlEmpty v-else-if="bundles.length === 0" icon="" style="--pane-pad: 40px 24px">
      <p>{{ t('bundles.store.empty') }}</p>
      <HlButton size="sm" :disabled="running" @click="refresh">{{ t('bundles.store.refresh') }}</HlButton>
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
              {{ t('bundles.store.ending', { d: daysLeft(b.endAt) }) }}
            </span>
          </div>
          <div class="hb-card__body">
            <div class="hb-card__name">{{ b.name }}</div>
            <div class="hb-card__meta">
              <span v-if="(b as HumbleBundleItem).priceCnyFen !== null" class="hb-card__price">
                {{ formatCnyFen((b as HumbleBundleItem).priceCnyFen) }}
              </span>
              <span v-else class="hb-card__price na">—</span>
              <span v-if="soldText(b as HumbleBundleItem)" class="hb-card__sold">
                {{ soldText(b as HumbleBundleItem) }}
              </span>
              <span class="hb-card__count">{{ t('bundles.store.gameCount', { n: b.gameCount }) }}</span>
            </div>
            <div v-if="period(b)" class="hb-card__period">
              <HlIcon name="calendar" :size="11" />
              {{ t('bundles.store.period') }} {{ period(b) }}
            </div>
          </div>
        </button>
      </div>
      <div v-if="hiddenCount > 0 || expanded" class="hb-more">
        <HlButton size="sm" variant="text" @click="expanded = !expanded">
          {{ expanded
            ? t('bundles.store.collapse')
            : t('bundles.store.expand', { n: hiddenCount }) }}
        </HlButton>
      </div>
    </template>

    <!-- 包详情抽屉：共享组件（档位条 + 卡网格 + 欠账行，宽度记忆沿用旧键） -->
    <BundleDetailDrawer
      v-model:open="open"
      :loading="detailLoading"
      :title="detail?.name ?? ''"
      :price-display="drawerPrice"
      :sold-display="drawerSold"
      :game-count="detail?.gameCount ?? 0"
      :period="drawerPeriod"
      :url="detail?.url ?? ''"
      kind="cumulative"
      currency="CNY"
      :tiers="detailTiers"
      :items="(detailItems as GameListItem[])"
      :pending="detailPending"
      :storage-key="`${APP_SLUG}.humble.drawer-w`"
    />
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
/* 收录中 N 触发芯片：数字卡 hover 亲和态口径（cursor+边框/底色反馈） */
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
/* 倒计时三档：紧迫红 / 警示 / 弱化 */
.hb-card__ending.is-hot { background: var(--danger); color: var(--ink-on-fill); }
.hb-card__ending.is-warn { background: var(--warning); color: var(--ink-on-fill); }
.hb-card__ending.is-cool {
  background: var(--surface-chip-2);
  color: var(--text-secondary);
  font-weight: 500;
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
  gap: 8px;
}
.hb-card__price {
  font-size: 14px;
  font-weight: 700;
  color: var(--success);
  font-variant-numeric: tabular-nums;
}
.hb-card__price.na { color: var(--text-dim); }
/* 销量（0.4.0）：弱化小字，价格之后、条目数之前 */
.hb-card__sold {
  font-size: 11px;
  color: var(--text-dim);
  font-variant-numeric: tabular-nums;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hb-card__count {
  margin-left: auto;
  font-size: 11.5px;
  color: var(--text-secondary);
  flex-shrink: 0;
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
</style>
