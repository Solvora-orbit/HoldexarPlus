<script setup lang="ts">
/* 榜单视图：抓取发现的可视化（特惠榜 / 热销榜 / 热门新品 / 即将推出）。
   数据源 GET /games?board=<key>——榜序 appid join 游戏目录与当前价格行，
   组装与找游戏列表同款（_build_list_item），卡片直接复用 HlGameCard。
   榜单源三级缓存（热 1h → 实时 → stale 兜底）；「新面孔」要等目录层
   收编（跑一轮抓取落库）才会出现在这里。 */
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { gamesApi, type GameListItem } from '@/api/client'
import { useRegionsStore } from '@/stores/regions'
import { useI18n, type MessageKey } from '@/locales'
import HlGameCard from '@/components/business/HlGameCard.vue'
import HlButton from '@/components/ui/HlButton.vue'
import HlEmpty from '@/components/ui/HlEmpty.vue'
import HlSpinner from '@/components/ui/HlSpinner.vue'
import { message } from '@/components/ui'

type BoardKey = 'specials' | 'topsellers' | 'popularnew' | 'comingsoon'

const route = useRoute()
const router = useRouter()
const regionsStore = useRegionsStore()
const { t } = useI18n()

const PAGE = 60
const KEYS: { key: BoardKey; labelKey: MessageKey }[] = [
  { key: 'specials', labelKey: 'library.board.specials' },
  { key: 'topsellers', labelKey: 'library.board.topsellers' },
  { key: 'popularnew', labelKey: 'library.board.popularnew' },
  { key: 'comingsoon', labelKey: 'library.board.comingsoon' },
]

function isBoardKey(v: unknown): v is BoardKey {
  return typeof v === 'string' && KEYS.some((k) => k.key === v)
}

const boardKey = ref<BoardKey>(isBoardKey(route.query.board) ? route.query.board : 'specials')
const items = ref<GameListItem[]>([])
const total = ref(0)
const hasMore = ref(false)
const cursor = ref<string | null>(null)
const loading = ref(false)
const error = ref('')

async function load(reset = false) {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const res = await gamesApi.list({
      board: boardKey.value,
      limit: PAGE,
      after: reset ? null : cursor.value,
    })
    items.value = reset ? res.items : [...items.value, ...res.items]
    total.value = res.total
    hasMore.value = res.hasMore
    cursor.value = res.nextCursor
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

function pick(key: BoardKey) {
  if (key === boardKey.value) return
  boardKey.value = key
  items.value = []
  total.value = 0
  hasMore.value = false
  cursor.value = null
  // 榜名进 URL：刷新/分享都停在同一个榜
  void router.replace({ query: { ...route.query, board: key } })
  void load(true)
}

onMounted(() => void load(true))
</script>

<template>
  <section class="board-page">
    <!-- 榜单 chips：榜名只存 key，渲染期 t() 保持语言响应式 -->
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
        <HlButton size="sm" :loading="loading" @click="load(false)">{{ t('library.board.loadMore') }}</HlButton>
      </div>
      <div class="board-more" v-else-if="!loading">
        <span class="board-more__end">{{ t('library.end') }}</span>
      </div>
    </template>

    <!-- 兜底提示：message 组件的非阻塞告警（避免错误态覆盖后仍想回列表） -->
    <div class="board-back">
      <HlButton size="sm" variant="text" @click="router.push('/library')">
        {{ t('library.board.backToList') }}
      </HlButton>
    </div>
  </section>
</template>

<style scoped>
.board-page {
  max-width: 1200px;
  margin: 0 auto;
}
.board-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 4px 0 8px;
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
.board-back {
  display: flex;
  justify-content: center;
  margin-top: 10px;
}
</style>
