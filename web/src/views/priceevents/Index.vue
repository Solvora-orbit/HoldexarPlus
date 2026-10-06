<script setup lang="ts">
/* 降价动态子页（plus.3）：仪表盘降价动态的完整列表。
   数据源 GET /games?flag=any|hl&sort=updated|discount——flag=any 是
   新史低/平史低/永降/折扣中的并集（与仪表盘 feed 同口径），hl 只留
   新史低；游标分页 + 竞态序号守卫（切筛选只认最后一次响应，
   AGENTS 前端交互规范）。 */
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { gamesApi, type GameListItem } from '@/api/client'
import { useRegionsStore } from '@/stores/regions'
import { useGoBack } from '@/composables/useGoBack'
import { useI18n, type MessageKey } from '@/locales'
import HlGameCard from '@/components/business/HlGameCard.vue'
import HlBacktop from '@/components/ui/HlBacktop.vue'
import HlButton from '@/components/ui/HlButton.vue'
import HlChip from '@/components/ui/HlChip.vue'
import HlEmpty from '@/components/ui/HlEmpty.vue'
import HlSpinner from '@/components/ui/HlSpinner.vue'

const route = useRoute()
const router = useRouter()
const regionsStore = useRegionsStore()
const { t } = useI18n()
const { goBack } = useGoBack('/dashboard')

const PAGE = 40

type FlagKey = 'any' | 'hl'
type SortKey = 'updated' | 'discount'
const FLAGS: { key: FlagKey; labelKey: MessageKey }[] = [
  { key: 'any', labelKey: 'priceEvents.filter.all' },
  { key: 'hl', labelKey: 'priceEvents.filter.newLow' },
]
const SORTS: { key: SortKey; labelKey: MessageKey }[] = [
  { key: 'updated', labelKey: 'priceEvents.sort.updated' },
  { key: 'discount', labelKey: 'priceEvents.sort.discount' },
]
function isFlag(v: unknown): v is FlagKey {
  return v === 'any' || v === 'hl'
}
function isSort(v: unknown): v is SortKey {
  return v === 'updated' || v === 'discount'
}

const flag = ref<FlagKey>(isFlag(route.query.flag) ? route.query.flag : 'any')
const sort = ref<SortKey>(isSort(route.query.sort) ? route.query.sort : 'updated')

const items = ref<GameListItem[]>([])
const total = ref(0)
const hasMore = ref(false)
const cursor = ref<string | null>(null)
const loading = ref(false)
const loadingMore = ref(false)
const error = ref('')
/** 竞态守卫：切筛选即推进序号，过期响应不落地也不收状态 */
let reqSeq = 0

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
      flag: flag.value,
      sort: sort.value,
      limit: PAGE,
      after: reset ? null : cursor.value,
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

function syncQuery() {
  void router.replace({
    query: {
      ...route.query,
      flag: flag.value === 'any' ? undefined : flag.value,
      sort: sort.value === 'updated' ? undefined : sort.value,
    },
  })
}

function reload() {
  items.value = []
  total.value = 0
  hasMore.value = false
  cursor.value = null
  syncQuery()
  void load(true)
}

function pickFlag(key: FlagKey) {
  if (key === flag.value) return
  flag.value = key
  reload()
}

function pickSort(key: SortKey) {
  if (key === sort.value) return
  sort.value = key
  reload()
}

onMounted(() => void load(true))
</script>

<template>
  <section class="pe-page">
    <div class="pe-toolbar">
      <HlButton size="sm" variant="text" @click="goBack">← {{ t('library.board.back') }}</HlButton>
      <span class="pe-desc">{{ t('priceEvents.desc') }}</span>
      <div class="pe-toolbar__spacer"></div>
      <HlChip
        v-for="f in FLAGS"
        :key="f.key"
        shape="soft"
        :on="flag === f.key"
        @click="pickFlag(f.key)"
      >{{ t(f.labelKey) }}</HlChip>
      <span class="pe-toolbar__divider"></span>
      <HlChip
        v-for="s in SORTS"
        :key="s.key"
        shape="soft"
        :on="sort === s.key"
        @click="pickSort(s.key)"
      >{{ t(s.labelKey) }}</HlChip>
    </div>

    <div v-if="loading" class="pe-loading">
      <HlSpinner />
      {{ t('library.loadingMore') }}
    </div>
    <HlEmpty v-else-if="error" icon="" style="--pane-pad: 40px 24px">
      <h3>{{ t('library.error.title') }}</h3>
      <p>{{ error }}</p>
      <HlButton size="sm" @click="load(true)">{{ t('common.retry') }}</HlButton>
    </HlEmpty>
    <HlEmpty v-else-if="!items.length" icon="" style="--pane-pad: 40px 24px">
      <p>{{ t('priceEvents.empty') }}</p>
      <HlButton size="sm" @click="router.push('/crawl')">{{ t('library.board.gotoCrawl') }}</HlButton>
    </HlEmpty>
    <template v-else>
      <div class="pe-grid">
        <HlGameCard
          v-for="game in items"
          :key="game.appid"
          :game="game"
          layout-mode="grid"
          :enabled-regions="regionsStore.enabledCodes"
        />
      </div>
      <div class="pe-more" v-if="hasMore">
        <HlButton size="sm" :loading="loadingMore" @click="load(false)">{{ t('library.board.loadMore') }}</HlButton>
      </div>
      <div class="pe-more" v-else>
        <span class="pe-more__end">{{ t('library.end') }}</span>
      </div>
    </template>

    <HlBacktop target=".view-container" />
  </section>
</template>

<style scoped>
.pe-page {
  max-width: 1200px;
  margin: 0 auto;
}
.pe-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin: 4px 0 12px;
}
.pe-toolbar__spacer {
  flex: 1;
}
.pe-toolbar__divider {
  width: 1px;
  height: 18px;
  background: var(--border-soft);
}
.pe-desc {
  font-size: 12.5px;
  color: var(--text-secondary);
}
.pe-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 40px 0;
  color: var(--text-secondary);
}
.pe-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 12px;
  grid-auto-rows: 1fr;
}
.pe-grid > :deep(.game-card) {
  height: 100%;
}
.pe-more {
  display: flex;
  justify-content: center;
  padding: 14px 0 4px;
}
.pe-more__end {
  font-size: 12px;
  color: var(--text-secondary);
}
</style>
