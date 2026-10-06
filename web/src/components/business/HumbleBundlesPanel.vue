<script setup lang="ts">
/* HB 捆绑包面板（捆绑包中心「HB 捆绑包」源，0.2.0）。
   数据源 humbleApi（后端每日抓取 zh.humblebundle.com/games 列表 + 在售包
   详情；纯本地读）。包卡 → 抽屉内游戏卡网格（与月包面板同款 HlGameCard，
   点击进详情看各区价格——「方便查看比价」）。
   价格：后端已把页面 preset_prices 最低档折算成人民币分（zh 站点输出
   CNY，无需二次换算）。 */
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue'

import { humbleApi, type HumbleBundleItem } from '@/api/client'
import { formatCnyFen } from '@/api/regions'
import { useRegionsStore } from '@/stores/regions'
import { useI18n } from '@/locales'
import HlGameCard from '@/components/business/HlGameCard.vue'
import { HlButton, HlDrawer, HlEmpty, HlIcon, HlSpinner, message } from '@/components/ui'
import { HlImg } from '@/components/ui'
import type { GameListItem } from '@/api/client'

const { t } = useI18n()
const regionsStore = useRegionsStore()

const bundles = ref<HumbleBundleItem[]>([])
const loading = ref(true)
const running = ref(false)
let firstLoad = true

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

onMounted(() => {
  void load()
  firstLoad = false
})
onActivated(() => {
  /* keep-alive 切回：静默重拉保鲜；若刷新任务在跑则续轮询 */
  void load()
})
onDeactivated(stopPoll)
onBeforeUnmount(stopPoll)

/* 上架/剩余天数：endAt 为空 = 长期在售 */
function daysLeft(endAt: string | null): number | null {
  if (!endAt) return null
  const ms = new Date(endAt).getTime() - Date.now()
  return ms > 0 ? Math.max(1, Math.ceil(ms / 86400000)) : 0
}

// ─── 包详情抽屉 ───
const open = ref(false)
const detailLoading = ref(false)
const detailSlug = ref('')
const detailName = ref('')
const detailPrice = ref<number | null>(null)
const detailItems = ref<GameListItem[]>([])
const detailTotal = ref(0)
const detailUrl = ref('')

const detailCount = computed(() => t('bundles.humble.gameCount', { n: detailTotal.value }))

async function openBundle(b: HumbleBundleItem) {
  detailSlug.value = b.slug
  detailName.value = b.name
  detailPrice.value = b.priceCnyFen
  detailUrl.value = b.url
  detailItems.value = []
  detailTotal.value = 0
  open.value = true
  detailLoading.value = true
  try {
    const res = await humbleApi.bundleDetail(b.slug)
    detailItems.value = res.items
    detailTotal.value = res.total
    detailName.value = res.name || detailName.value
    detailPrice.value = res.priceCnyFen ?? detailPrice.value
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    detailLoading.value = false
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

    <div v-if="loading || firstLoad" class="hb-panel__loading">
      <HlSpinner /> {{ t('library.loadingMore') }}
    </div>
    <HlEmpty v-else-if="bundles.length === 0" icon="" style="--pane-pad: 40px 24px">
      <p>{{ t('bundles.humble.empty') }}</p>
      <HlButton size="sm" :disabled="running" @click="refresh">{{ t('bundles.humble.refresh') }}</HlButton>
    </HlEmpty>
    <template v-else>
      <div class="hb-grid">
        <button
          v-for="b in bundles"
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
          </div>
        </button>
      </div>
    </template>

    <!-- 包详情抽屉：包内游戏卡网格（点击进详情比价，与月包同款） -->
    <HlDrawer v-model="open" :title="detailName" :width="880">
      <div class="hb-drawer__head">
        <span v-if="detailPrice !== null" class="hb-drawer__price">{{ formatCnyFen(detailPrice) }}</span>
        <span class="hb-drawer__count">{{ detailCount }}</span>
        <a
          v-if="detailUrl"
          class="hb-drawer__official"
          :href="detailUrl"
          target="_blank"
          rel="noopener noreferrer"
        >{{ t('bundles.humble.official') }} ↗</a>
      </div>
      <div v-if="detailLoading" class="hb-panel__loading">
        <HlSpinner /> {{ t('library.loadingMore') }}
      </div>
      <HlEmpty v-else-if="detailItems.length === 0" icon="" size="sm">
        <p>{{ t('bundles.humble.detailEmpty') }}</p>
      </HlEmpty>
      <div v-else class="hb-drawer__grid">
        <HlGameCard
          v-for="game in detailItems"
          :key="game.appid"
          :game="game"
          layout-mode="grid"
          :enabled-regions="regionsStore.enabledCodes"
        />
      </div>
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
.hb-drawer__head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.hb-drawer__price {
  font-size: 16px;
  font-weight: 700;
  color: var(--success);
  font-variant-numeric: tabular-nums;
}
.hb-drawer__count {
  font-size: 12px;
  color: var(--text-secondary);
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
</style>
