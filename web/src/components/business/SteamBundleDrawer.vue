<script setup lang="ts">
/* Steam 捆绑包详情二级抽屉（0.6.0）：三站条目命中 Steam 捆绑包后点开——
 * 非模态叠加在包抽屉之上（PriceTrendDrawer 先例），样式对齐
 * SteamBundlesPanel 包抽屉：封面 + 国区/最低区/差价对比 + 包内游戏行，
 * 行点击直达站内游戏详情页。数据由父级加载缓存传入（bundleState），
 * 打开零重复请求。 */
import { useRouter } from 'vue-router'

import type { BundleDetail, BundleGame } from '@/api/client'
import { formatCnyFen } from '@/api/regions'
import { useI18n } from '@/locales'
import { HlDrawer, HlEmpty, HlImg, HlSpinner } from '@/components/ui'

const props = defineProps<{
  /** 已加载详情（null = 未取到；配合 loading 显示骨架） */
  detail: BundleDetail | null
  loading: boolean
  /** 详情未回时的标题兜底（站源侧名） */
  fallbackTitle?: string
}>()

const visible = defineModel<boolean>({ default: false })

const { t } = useI18n()
const router = useRouter()

/** 包内游戏的最低区折算价（分）：无有效价返回 null（未收录/未取价） */
function gameLowest(g: BundleGame): number | null {
  let best: number | null = null
  for (const p of Object.values(g.prices || {})) {
    if (p.cnyFen && p.cnyFen > 0 && (best === null || p.cnyFen < best)) best = p.cnyFen
  }
  return best
}

function goGame(appid: number) {
  visible.value = false
  router.push(`/game/${appid}`)
}
</script>

<template>
  <HlDrawer
    v-model="visible"
    :modal="false"
    :with-header="false"
    width="620px"
    :top="0"
    class="sbd"
  >
    <div class="sbd__body">
      <button type="button" class="sbd__close" :title="t('common.close')" @click="visible = false">✕</button>
      <div class="sbd__head">
        <span class="sbd__tag">{{ t('bundles.store.bundleTag') }}</span>
        <h3 class="sbd__name">{{ props.detail?.name || props.fallbackTitle || '' }}</h3>
        <a
          v-if="props.detail?.url"
          class="sbd__official"
          :href="props.detail.url"
          target="_blank"
          rel="noopener noreferrer"
        >{{ t('bundles.steam.official') }} ↗</a>
      </div>

      <div v-if="props.loading || !props.detail" class="sbd__loading">
        <HlSpinner /> {{ t('library.loadingMore') }}
      </div>
      <template v-else>
        <div class="sbd__cover">
          <HlImg :src="props.detail.headerImage" :alt="props.detail.name" loading="lazy">
            <template #fallback>
              <div class="sbd__cover-fb">{{ (props.detail.name || '?').slice(0, 1) }}</div>
            </template>
          </HlImg>
        </div>
        <div class="sbd__prices">
          <span v-if="props.detail.cnCnyFen !== null" class="sbd__cn">
            {{ t('bundles.steam.cnPrice') }} {{ formatCnyFen(props.detail.cnCnyFen) }}
          </span>
          <span v-if="props.detail.lowestCnyFen !== null" class="sbd__lowest">
            {{ t('bundles.steam.lowest') }} {{ props.detail.lowestRegion || '' }}
            {{ formatCnyFen(props.detail.lowestCnyFen) }}
          </span>
          <span v-if="props.detail.diffFen > 0" class="sbd__diff">
            {{ t('bundles.steam.diff', { price: formatCnyFen(props.detail.diffFen) }) }}
          </span>
        </div>
        <p class="sbd__count">{{ t('bundles.steam.gameCount', { n: props.detail.appIds.length }) }}</p>
        <HlEmpty v-if="!props.detail.games || props.detail.games.length === 0" icon="" size="sm">
          <p>{{ t('bundles.steam.detailEmpty') }}</p>
        </HlEmpty>
        <ul v-else class="sbd-games">
          <li
            v-for="g in props.detail.games"
            :key="g.appid"
            class="sbd-game"
            :class="{ 'is-link': !!g.name }"
            :role="g.name ? 'link' : undefined"
            :tabindex="g.name ? 0 : undefined"
            @click="g.name ? goGame(g.appid) : undefined"
            @keydown.enter="g.name ? goGame(g.appid) : undefined"
          >
            <HlImg :src="g.headerImage" :alt="g.name || String(g.appid)" loading="lazy" class="sbd-game__img">
              <template #fallback>
                <div class="sbd-game__fb">{{ (g.name || String(g.appid)).slice(0, 1) }}</div>
              </template>
            </HlImg>
            <div class="sbd-game__body">
              <span class="sbd-game__name">
                {{ g.name || t('bundles.steam.appidFallback', { id: g.appid }) }}
              </span>
              <div class="sbd-game__prices">
                <span v-if="g.prices?.CN?.cnyFen" class="sbd-game__cn">
                  {{ t('bundles.steam.cnPrice') }} {{ formatCnyFen(g.prices.CN.cnyFen) }}
                </span>
                <span v-if="gameLowest(g)" class="sbd-game__lowest">
                  {{ t('bundles.steam.lowest') }} {{ formatCnyFen(gameLowest(g)!) }}
                </span>
              </div>
            </div>
          </li>
        </ul>
      </template>
    </div>
  </HlDrawer>
</template>

<style scoped>
.sbd__body {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.sbd__close {
  position: absolute;
  top: -2px;
  right: 0;
  border: none;
  background: transparent;
  font-size: 15px;
  line-height: 1;
  color: var(--text-secondary);
  cursor: pointer;
  padding: 4px 6px;
  border-radius: 6px;
}
.sbd__close:hover { color: var(--text-primary); background: var(--surface-inset); }
.sbd__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding-right: 30px;
}
.sbd__tag {
  flex-shrink: 0;
  font-size: 10.5px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--accent);
  color: var(--ink-on-fill);
}
.sbd__name {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  line-height: 1.4;
}
.sbd__official {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 12px;
  color: var(--accent);
  text-decoration: none;
}
.sbd__official:hover { text-decoration: underline; }
.sbd__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 32px 0;
  color: var(--text-secondary);
}
.sbd__cover {
  position: relative;
  aspect-ratio: 16 / 9;
  border-radius: 8px;
  overflow: hidden;
  background: var(--surface-inset);
}
.sbd__cover :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.sbd__cover-fb {
  width: 100%;
  height: 100%;
  display: grid;
  place-items: center;
  font-size: 28px;
  font-weight: 700;
  color: var(--text-dim);
}
.sbd__prices {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 12px;
  font-size: 12px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}
.sbd__cn { font-weight: 600; color: var(--text-primary); }
.sbd__lowest { color: var(--success); }
.sbd__diff { color: var(--accent); }
.sbd__count {
  margin: 0;
  font-size: 11.5px;
  color: var(--text-secondary);
}
.sbd-games {
  list-style: none;
  margin: 4px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.sbd-game {
  display: flex;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid var(--line-1);
  border-radius: 8px;
  background: var(--surface-inset);
  transition: border-color var(--transition), background var(--transition);
}
.sbd-game.is-link {
  cursor: pointer;
}
.sbd-game.is-link:hover {
  border-color: var(--accent-a50);
  background: var(--surface-panel);
}
.sbd-game.is-link:hover .sbd-game__name { color: var(--accent); }
.sbd-game__img {
  width: 120px;
  aspect-ratio: 16 / 9;
  object-fit: cover;
  border-radius: 6px;
  flex-shrink: 0;
  background: var(--surface-panel);
}
.sbd-game__fb {
  width: 120px;
  height: 68px;
  display: grid;
  place-items: center;
  font-size: 18px;
  font-weight: 700;
  color: var(--text-dim);
  border-radius: 6px;
}
.sbd-game__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  justify-content: center;
}
.sbd-game__name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sbd-game__prices {
  display: flex;
  gap: 12px;
  font-size: 11.5px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}
.sbd-game__lowest { color: var(--success); }
</style>
