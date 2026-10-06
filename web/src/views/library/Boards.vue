<script setup lang="ts">
/* 榜单视图：抓取发现的可视化（特惠榜 / 热销榜 / 热门新品 / 即将推出）。
   数据源 GET /games?board=<key>——榜序 appid join 游戏目录与当前价格行，
   组装与找游戏列表同款（_build_list_item），卡片直接复用 HlGameCard。
   榜单源三级缓存（热 1h → 实时 → stale 兜底）；「新面孔」要等目录层
   收编（跑一轮抓取落库）才会出现在这里。

   加载竞态（plus.3）：切榜/换筛选一律推进请求序号，只认最后一次请求的
   响应，过期响应整体丢弃。此前是 `if (loading) return` 防重入——旧榜请求
   在途时新榜的加载会被静默丢掉，快速切榜出现「特惠榜显示即将推出」的串数据。

   筛选（plus.3）：区域视角 + 比价模式 + 仅折中，直传 /games?board= 已支持的
   常规筛选参数（榜单分支 join 价格行后套同一套筛选，后端零改动）；
   可深链状态进 URL query（AGENTS 前端交互规范）。 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { gamesApi, type GameListItem } from '@/api/client'
import { regionSelectOptions } from '@/api/selectOptions'
import { useGoBack } from '@/composables/useGoBack'
import { useRegionsStore } from '@/stores/regions'
import { useI18n, type MessageKey } from '@/locales'
import HlGameCard from '@/components/business/HlGameCard.vue'
import HlBacktop from '@/components/ui/HlBacktop.vue'
import HlButton from '@/components/ui/HlButton.vue'
import HlChip from '@/components/ui/HlChip.vue'
import HlEmpty from '@/components/ui/HlEmpty.vue'
import HlSelect from '@/components/ui/HlSelect.vue'
import HlSpinner from '@/components/ui/HlSpinner.vue'

type BoardKey = 'specials' | 'topsellers' | 'popularnew' | 'comingsoon'
type FilterMode = 'global' | 'cheaper' | 'highdiff'

const route = useRoute()
const router = useRouter()
const regionsStore = useRegionsStore()
const { t } = useI18n()
const { goBack } = useGoBack('/library')

const PAGE = 60
const KEYS: { key: BoardKey; labelKey: MessageKey }[] = [
  { key: 'specials', labelKey: 'library.board.specials' },
  { key: 'topsellers', labelKey: 'library.board.topsellers' },
  { key: 'popularnew', labelKey: 'library.board.popularnew' },
  { key: 'comingsoon', labelKey: 'library.board.comingsoon' },
]

const FILTER_MODES: { key: FilterMode; labelKey: MessageKey }[] = [
  { key: 'global', labelKey: 'navbar.filterMode.global' },
  { key: 'cheaper', labelKey: 'navbar.filterMode.cheaper' },
  { key: 'highdiff', labelKey: 'navbar.filterMode.highDiff' },
]

function isBoardKey(v: unknown): v is BoardKey {
  return typeof v === 'string' && KEYS.some((k) => k.key === v)
}

function isFilterMode(v: unknown): v is FilterMode {
  return v === 'global' || v === 'cheaper' || v === 'highdiff'
}

/* 可深链状态从 query 还原（刷新/分享停在同一榜同一视角） */
function queryStr(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

const boardKey = ref<BoardKey>(isBoardKey(route.query.board) ? route.query.board : 'specials')
const region = ref(queryStr(route.query.region))
const filterMode = ref<FilterMode>(isFilterMode(route.query.filterMode) ? route.query.filterMode : 'global')
const onlyDiscounted = ref(route.query.discount === '1')

const items = ref<GameListItem[]>([])
const total = ref(0)
const hasMore = ref(false)
const cursor = ref<string | null>(null)
const loading = ref(false)
const loadingMore = ref(false)
const error = ref('')
/** 竞态守卫：切榜/换筛选即推进序号，过期响应（含其错误）不落地也不收状态 */
let reqSeq = 0

/** 榜单选项 = 全区最低 + 国区 + 启用区（与找游戏地区下拉同一来源出口） */
const regionOptions = computed(() => [
  { value: '', label: t('navbar.regionOption.allLowest') },
  ...regionSelectOptions(regionsStore.enabledCodes.map((c) => c.toUpperCase())),
])

function filterParams() {
  return {
    region: region.value,
    filterMode: region.value ? filterMode.value : undefined,
    onlyDiscounted: onlyDiscounted.value || undefined,
  }
}

/** 状态进 URL：筛选取默认值时省略键，URL 保持短 */
function syncQuery() {
  void router.replace({
    query: {
      ...route.query,
      board: boardKey.value,
      region: region.value || undefined,
      filterMode: region.value && filterMode.value !== 'global' ? filterMode.value : undefined,
      discount: onlyDiscounted.value ? '1' : undefined,
    },
  })
}

function resetList() {
  items.value = []
  total.value = 0
  hasMore.value = false
  cursor.value = null
}

async function load(reset = false) {
  const seq = ++reqSeq
  if (reset) {
    loading.value = true
    error.value = ''
  } else {
    if (loadingMore.value) return
    loadingMore.value = true
  }
  try {
    const res = await gamesApi.list({
      board: boardKey.value,
      limit: PAGE,
      after: reset ? null : cursor.value,
      ...filterParams(),
    })
    if (seq !== reqSeq) return
    items.value = reset ? res.items : [...items.value, ...res.items]
    total.value = res.total
    hasMore.value = res.hasMore
    cursor.value = res.nextCursor
  } catch (e) {
    if (seq !== reqSeq) return
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    if (seq === reqSeq) {
      loading.value = false
      loadingMore.value = false
    }
  }
}

/** 切榜/换筛选共用：清列表 → 同步 URL → 重拉首屏 */
function reload() {
  resetList()
  syncQuery()
  void load(true)
}

function pick(key: BoardKey) {
  if (key === boardKey.value) return
  boardKey.value = key
  reload()
}

function selectRegion(code: string | number) {
  region.value = String(code)
  // 与找游戏选区同语义：国区视角没有「比国区低/大差价」可言
  if (region.value === 'cn') filterMode.value = 'global'
  reload()
}

function selectFilterMode(mode: FilterMode) {
  if (filterMode.value === mode) return
  filterMode.value = mode
  reload()
}

function toggleOnlyDiscounted() {
  onlyDiscounted.value = !onlyDiscounted.value
  reload()
}

onMounted(() => void load(true))
</script>

<template>
  <section class="board-page">
    <!-- 工具行：返回 + 榜单 chips + 筛选（区域 / 仅折中） -->
    <div class="board-toolbar">
      <HlButton size="sm" variant="text" @click="goBack">← {{ t('library.board.back') }}</HlButton>
      <div class="board-chips">
        <button
          v-for="k in KEYS"
          :key="k.key"
          class="board-chip"
          :class="{ active: boardKey === k.key }"
          @click="pick(k.key)"
        >
          {{ t(k.labelKey) }}
        </button>
      </div>
      <div class="board-toolbar__spacer"></div>
      <HlSelect
        class="board-region"
        :model-value="region"
        :options="regionOptions"
        @update:model-value="selectRegion"
      />
      <HlChip shape="soft" :on="onlyDiscounted" @click="toggleOnlyDiscounted">
        {{ t('library.board.onlyDiscounted') }}
      </HlChip>
    </div>

    <!-- 比价模式子行：选中非国区才出现（与找游戏选区同语义） -->
    <div v-if="region && region !== 'cn'" class="board-modes">
      <HlChip
        v-for="mode in FILTER_MODES"
        :key="mode.key"
        shape="soft"
        :on="filterMode === mode.key"
        @click="selectFilterMode(mode.key)"
      >{{ t(mode.labelKey) }}</HlChip>
    </div>

    <div class="board-meta" v-if="!error">
      <span>{{ t('library.board.desc') }}</span>
      <span class="board-meta__count" v-if="total">{{ t('library.board.count', { n: total }) }}</span>
    </div>

    <!-- 加载态 -->
    <div v-if="loading" class="board-loading">
      <HlSpinner />
      {{ t('library.loadingMore') }}
    </div>

    <!-- 错误态（含榜单拉取失败 FETCH_FAILED） -->
    <HlEmpty v-else-if="error" icon="" style="--pane-pad: 40px 24px">
      <h3>{{ t('library.error.title') }}</h3>
      <p>{{ error }}</p>
      <HlButton size="sm" @click="load(true)">{{ t('common.retry') }}</HlButton>
    </HlEmpty>

    <!-- 空态：榜没拉到或新面孔尚未入目录——给出可操作的下一步 -->
    <HlEmpty v-else-if="!items.length" icon="" style="--pane-pad: 40px 24px">
      <p>{{ t('library.board.empty') }}</p>
      <HlButton size="sm" @click="router.push('/crawl')">{{ t('library.board.gotoCrawl') }}</HlButton>
    </HlEmpty>

    <template v-else>
      <!-- grid-auto-rows: 1fr + 卡片拉伸：同一行卡片等高（可选择的区块行由
           HlGameCard 的空占位槽补齐），消除此前参差不齐的排版 -->
      <div class="board-grid">
        <HlGameCard
          v-for="game in items"
          :key="game.appid"
          :game="game"
          layout-mode="grid"
          :enabled-regions="regionsStore.enabledCodes"
        />
      </div>
      <div class="board-more" v-if="hasMore">
        <HlButton size="sm" :loading="loadingMore" @click="load(false)">{{ t('library.board.loadMore') }}</HlButton>
      </div>
      <div class="board-more" v-else-if="!loading">
        <span class="board-more__end">{{ t('library.end') }}</span>
      </div>
    </template>

    <HlBacktop target=".view-container" />
  </section>
</template>

<style scoped>
.board-page {
  max-width: 1200px;
  margin: 0 auto;
}
.board-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin: 4px 0 8px;
}
.board-toolbar__spacer {
  flex: 1;
}
.board-region {
  width: 200px;
}
.board-modes {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.board-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.board-chip {
  padding: 6px 14px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--bg-soft);
  color: var(--text-primary);
  cursor: pointer;
  font-size: 13px;
}
.board-chip.active {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-a10);
}
.board-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 10px;
}
.board-meta__count {
  flex: 0 0 auto;
}
.board-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 40px 0;
  color: var(--text-secondary);
}
.board-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 12px;
  grid-auto-rows: 1fr;
}
.board-grid > :deep(.game-card) {
  height: 100%;
}
.board-more {
  display: flex;
  justify-content: center;
  padding: 14px 0 4px;
}
.board-more__end {
  font-size: 12px;
  color: var(--text-secondary);
}
</style>
