<script setup lang="ts">
/* Steam 捆绑包展示面板（捆绑包中心「Steam 捆绑包」源，0.2.0）。
   数据源现成 bundlesApi（GET /bundles + 单包详情）：展示已导入的
   Steam 捆绑包/Sub——包卡 → 抽屉内逐游戏行（封面/名称/CN 价/最低区
   价折算），点击直达站内详情页比价。旧捆绑包浏览页的补齐计算器等
   重功能仍保留在 views/bundles/Index.vue（脱离路由备回归）。 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { bundlesApi, type BundleGame, type BundleSummary } from '@/api/client'
import { formatCnyFen } from '@/api/regions'
import { useI18n } from '@/locales'
import { HlButton, HlDrawer, HlEmpty, HlImg, HlSpinner, message } from '@/components/ui'

const { t } = useI18n()
const router = useRouter()

const bundles = ref<BundleSummary[]>([])
const loading = ref(true)

async function load() {
  try {
    const res = await bundlesApi.list('diff')
    bundles.value = res.bundles
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    loading.value = false
  }
}

onMounted(() => void load())

// ─── 单包抽屉 ───
const open = ref(false)
const detailLoading = ref(false)
const detailName = ref('')
const detailUrl = ref('')
const games = ref<BundleGame[]>([])

/** 包内游戏的最低区折算价（分）：无有效价返回 null（未收录/未取价） */
function lowestCnyFen(g: BundleGame): number | null {
  let best: number | null = null
  for (const p of Object.values(g.prices || {})) {
    if (p.cnyFen && p.cnyFen > 0 && (best === null || p.cnyFen < best)) best = p.cnyFen
  }
  return best
}

async function openBundle(b: BundleSummary) {
  detailName.value = b.name
  detailUrl.value = b.url
  games.value = []
  open.value = true
  detailLoading.value = true
  try {
    const d = await bundlesApi.detail(b.bundleId)
    games.value = d.games || []
    detailName.value = d.name || b.name
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    detailLoading.value = false
  }
}
</script>

<template>
  <div class="sb-panel">
    <p class="sb-panel__desc">{{ t('bundles.steam.desc') }}</p>

    <div v-if="loading" class="sb-panel__loading">
      <HlSpinner /> {{ t('library.loadingMore') }}
    </div>
    <HlEmpty v-else-if="bundles.length === 0" icon="" style="--pane-pad: 40px 24px">
      <p>{{ t('bundles.steam.empty') }}</p>
    </HlEmpty>
    <div v-else class="sb-grid">
      <button
        v-for="b in bundles"
        :key="b.bundleId"
        type="button"
        class="sb-card"
        @click="openBundle(b)"
      >
        <div class="sb-card__cover">
          <HlImg :src="b.headerImage" :alt="b.name" loading="lazy">
            <template #fallback>
              <div class="sb-card__cover-fallback">{{ b.name.slice(0, 1) }}</div>
            </template>
          </HlImg>
        </div>
        <div class="sb-card__body">
          <div class="sb-card__name">{{ b.name }}</div>
          <div class="sb-card__meta">
            <span v-if="b.cnCnyFen !== null" class="sb-card__price">{{ formatCnyFen(b.cnCnyFen) }}</span>
            <span v-else class="sb-card__price na">—</span>
            <span class="sb-card__count">{{ t('bundles.steam.gameCount', { n: b.appIds.length }) }}</span>
          </div>
        </div>
      </button>
    </div>

    <HlDrawer v-model="open" :title="detailName" :width="760">
      <div class="sb-drawer__head">
        <a
          v-if="detailUrl"
          class="sb-drawer__official"
          :href="detailUrl"
          target="_blank"
          rel="noopener noreferrer"
        >{{ t('bundles.steam.official') }} ↗</a>
      </div>
      <div v-if="detailLoading" class="sb-panel__loading">
        <HlSpinner /> {{ t('library.loadingMore') }}
      </div>
      <HlEmpty v-else-if="games.length === 0" icon="" size="sm">
        <p>{{ t('bundles.steam.detailEmpty') }}</p>
      </HlEmpty>
      <ul v-else class="sb-games">
        <li v-for="g in games" :key="g.appid" class="sb-game">
          <HlImg :src="g.headerImage" :alt="g.name || String(g.appid)" loading="lazy" class="sb-game__img">
            <template #fallback>
              <div class="sb-game__fallback">{{ (g.name || String(g.appid)).slice(0, 1) }}</div>
            </template>
          </HlImg>
          <div class="sb-game__body">
            <button type="button" class="sb-game__name" @click="router.push(`/game/${g.appid}`)">
              {{ g.name || t('bundles.steam.appidFallback', { id: g.appid }) }}
            </button>
            <div class="sb-game__prices">
              <span v-if="g.prices?.CN?.cnyFen" class="sb-game__cn">
                {{ t('bundles.steam.cnPrice') }} {{ formatCnyFen(g.prices.CN.cnyFen) }}
              </span>
              <span v-if="lowestCnyFen(g)" class="sb-game__lowest">
                {{ t('bundles.steam.lowest') }} {{ formatCnyFen(lowestCnyFen(g)!) }}
              </span>
            </div>
          </div>
        </li>
      </ul>
    </HlDrawer>
  </div>
</template>

<style scoped>
.sb-panel__desc {
  font-size: 12.5px;
  color: var(--text-secondary);
  margin: 0 0 8px;
}
.sb-panel__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 32px 0;
  color: var(--text-secondary);
}
.sb-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}
.sb-card {
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
.sb-card:hover {
  border-color: var(--accent-a50);
  transform: translateY(-2px);
  box-shadow: var(--shadow-lg);
}
.sb-card__cover {
  position: relative;
  aspect-ratio: 16 / 9;
  background: var(--surface-inset);
}
.sb-card__cover :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.sb-card__cover-fallback {
  width: 100%;
  height: 100%;
  display: grid;
  place-items: center;
  font-size: 28px;
  font-weight: 700;
  color: var(--text-dim);
}
.sb-card__body {
  padding: 10px 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.sb-card__name {
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.sb-card__meta {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.sb-card__price {
  font-size: 14px;
  font-weight: 700;
  color: var(--success);
  font-variant-numeric: tabular-nums;
}
.sb-card__price.na { color: var(--text-dim); }
.sb-card__count {
  font-size: 11.5px;
  color: var(--text-secondary);
}
.sb-drawer__head {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 6px;
}
.sb-drawer__official {
  font-size: 12px;
  color: var(--accent);
  text-decoration: none;
}
.sb-drawer__official:hover { text-decoration: underline; }
.sb-games {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.sb-game {
  display: flex;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid var(--line-1);
  border-radius: 8px;
  background: var(--surface-inset);
}
.sb-game__img {
  width: 120px;
  aspect-ratio: 16 / 9;
  object-fit: cover;
  border-radius: 6px;
  flex-shrink: 0;
  background: var(--surface-panel);
}
.sb-game__fallback {
  width: 120px;
  height: 68px;
  display: grid;
  place-items: center;
  font-size: 18px;
  font-weight: 700;
  color: var(--text-dim);
  border-radius: 6px;
}
.sb-game__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  justify-content: center;
}
.sb-game__name {
  border: none;
  background: transparent;
  padding: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  text-align: left;
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: inherit;
}
.sb-game__name:hover { color: var(--accent); }
.sb-game__prices {
  display: flex;
  gap: 12px;
  font-size: 11.5px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}
.sb-game__lowest { color: var(--success); }
</style>
