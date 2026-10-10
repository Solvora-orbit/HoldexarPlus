<script setup lang="ts">
/* Steampy 行情页（0.5.0 实验页，侧栏「更多」组随实验开关渲染）。
   数据 = 站点公开 CDK 行情透传（后端 /steampy/hot 代理，无账号、只读）：
   游戏 CDK 最低价/在售数/Steam 原价/史低。分页浏览 + 已加载页本地过滤
   （listHot 无服务端搜索）；行点击外链 Steampy/Steam 商店页。
   实验开关关闭时本页自守卫：显示引导回设置的空态（深链直进场景）。 */
import { computed, onMounted, ref } from 'vue'

import { steampyApi, type SteampyHotItem } from '@/api/client'
import { useExperimental } from '@/stores/experimental'
import { useI18n } from '@/locales'
import { HlBacktop, HlButton, HlEmpty, HlImg, HlInput, HlPagination, HlSpinner, message } from '@/components/ui'

defineOptions({ name: 'SteampyMarket' })

const { t } = useI18n()
const experimental = useExperimental()

const items = ref<SteampyHotItem[]>([])
const loading = ref(false)
const page = ref(1)
const pageSize = 20
/** 后端 listHot 的 total（分页控件用；抓不到时按「还有下一页」推进） */
const total = ref(0)
const search = ref('')

async function loadPage(p: number) {
  if (loading.value) return
  loading.value = true
  try {
    const res = await steampyApi.hot(p, pageSize)
    if (!res.enabled) {
      await experimental.load(true)
      return
    }
    if (res.ok === false) {
      message.error(t('steampy.loadFailed'))
      return
    }
    items.value = res.items
    page.value = p
    total.value = res.total ?? items.value.length
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    loading.value = false
  }
}

/* 本地过滤：listHot 无服务端搜索——对已加载的当前页按名称/AppID 过滤 */
const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return items.value
  return items.value.filter(
    (it) => it.gameName.toLowerCase().includes(q) || String(it.appId).includes(q),
  )
})

function steamUrl(appId: number): string {
  return `https://store.steampowered.com/app/${appId}/`
}

function discountText(it: SteampyHotItem): string {
  if (it.oriPrice > 0 && it.gamePrice > 0) {
    return `${Math.round((it.gamePrice / it.oriPrice) * 100)}%`
  }
  return '—'
}

onMounted(async () => {
  await experimental.load()
  void loadPage(1)
})
</script>

<template>
  <section class="steampy-page">
    <div class="steampy-head">
      <div class="steampy-head__text">
        <div class="steampy-head__title">
          {{ t('steampy.title') }}
          <span class="steampy-head__badge">{{ t('steampy.badge') }}</span>
        </div>
        <p class="steampy-head__desc">{{ t('steampy.desc') }}</p>
      </div>
      <HlInput
        v-model="search"
        :placeholder="t('steampy.searchPlaceholder')"
        class="steampy-head__search"
      />
    </div>

    <div v-if="!experimental.enabled" class="steampy-gate">
      <HlEmpty icon="" style="--pane-pad: 48px 24px">
        <p>{{ t('steampy.gated') }}</p>
        <HlButton size="sm" @click="$router.push('/settings')">
          {{ t('steampy.goSettings') }}
        </HlButton>
      </HlEmpty>
    </div>

    <template v-else>
      <div v-if="loading && items.length === 0" class="steampy-loading">
        <HlSpinner /> {{ t('library.loadingMore') }}
      </div>
      <HlEmpty v-else-if="filtered.length === 0" icon="" style="--pane-pad: 40px 24px">
        <p>{{ t('steampy.empty') }}</p>
      </HlEmpty>
      <template v-else>
        <div class="steampy-grid">
          <a
            v-for="it in filtered"
            :key="it.appId"
            class="steampy-card"
            :href="`https://steampy.com/CDKeyGameDetailMb?gameId=${it.appId}`"
            target="_blank"
            rel="noopener noreferrer"
          >
            <div class="steampy-card__cover">
              <HlImg :src="it.gameAva" :alt="it.gameName" loading="lazy">
                <template #fallback>
                  <span class="steampy-card__glyph" aria-hidden="true">{{ it.gameName.slice(0, 1) }}</span>
                </template>
              </HlImg>
              <span class="steampy-card__discount">{{ discountText(it) }}</span>
            </div>
            <div class="steampy-card__body">
              <div class="steampy-card__name" :title="it.gameName">{{ it.gameName }}</div>
              <div class="steampy-card__prices">
                <span class="steampy-card__key">¥{{ it.keyPrice.toFixed(2) }}</span>
                <span class="steampy-card__ori">¥{{ it.gamePrice.toFixed(2) }}</span>
                <span class="steampy-card__sales">{{ t('steampy.onSale', { n: it.keySales }) }}</span>
              </div>
              <div class="steampy-card__meta">
                <span>{{ t('steampy.lowest', { p: `¥${it.hisPrice.toFixed(2)}` }) }}</span>
                <span class="steampy-card__appid">{{ it.appId }}</span>
              </div>
            </div>
          </a>
        </div>
        <div class="steampy-foot">
          <HlPagination
            v-model="page"
            :total="Math.max(total, page * pageSize)"
            :page-size="pageSize"
            @update:model-value="loadPage"
          />
        </div>
      </template>
    </template>

    <HlBacktop target=".view-container" />
  </section>
</template>

<style scoped>
.steampy-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.steampy-head__title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 700;
  color: var(--text-primary);
}
.steampy-head__badge {
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px solid var(--warning-a40, var(--border-soft));
  color: var(--warning);
  font-size: 10.5px;
  font-weight: 700;
}
.steampy-head__desc {
  margin: 4px 0 0;
  font-size: 12.5px;
  color: var(--text-secondary);
  max-width: 640px;
}
.steampy-head__search {
  width: 240px;
  flex-shrink: 0;
}
.steampy-gate {
  padding: 24px 0;
}
.steampy-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 48px 0;
  color: var(--text-secondary);
}
.steampy-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 12px;
  margin-top: 12px;
}
.steampy-card {
  position: relative;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border-soft);
  border-radius: var(--radius);
  background: var(--bg-card);
  color: var(--text-primary);
  text-decoration: none;
  overflow: hidden;
  transition: border-color var(--transition), transform var(--transition), box-shadow var(--transition);
}
.steampy-card:hover {
  border-color: var(--accent-a50);
  transform: translateY(-2px);
  box-shadow: var(--shadow-lg);
}
.steampy-card__cover {
  position: relative;
  aspect-ratio: 16 / 9;
  background: var(--surface-inset);
}
.steampy-card__cover :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.steampy-card__glyph {
  width: 100%;
  height: 100%;
  display: grid;
  place-items: center;
  font-size: 28px;
  font-weight: 700;
  color: var(--text-dim);
}
.steampy-card__discount {
  position: absolute;
  top: 8px;
  right: 8px;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--danger);
  color: var(--ink-on-fill);
  font-size: 10.5px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.steampy-card__body {
  padding: 10px 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.steampy-card__name {
  font-size: 13px;
  font-weight: 600;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.steampy-card__prices {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.steampy-card__key {
  font-size: 14.5px;
  font-weight: 700;
  color: var(--success);
  font-variant-numeric: tabular-nums;
}
.steampy-card__ori {
  font-size: 11.5px;
  color: var(--text-dim);
  text-decoration: line-through;
  font-variant-numeric: tabular-nums;
}
.steampy-card__sales {
  margin-left: auto;
  font-size: 11px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}
.steampy-card__meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}
.steampy-card__appid {
  color: var(--text-dim);
}
.steampy-foot {
  display: flex;
  justify-content: center;
  padding: 16px 0 4px;
}
</style>
