<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useI18n } from '@/locales'

/**
 * 回顶按钮：默认跟随 App 外壳的滚动容器 `.view-container`（本应用的页面滚动
 * 都发生在那里，window 基本不滚）。target 变化（如找游戏网格/列表双滚动容器
 * 切换）时自动重挂监听；滚动超过 threshold 才出现，避免首屏的无意义按钮。
 * 定位（fixed 右下）在 .hl-backtop 框架样式里，消费方只管挂载与 target。
 */
const props = defineProps<{
  /** 滚动容器选择器；缺省回落 window */
  target?: string
  /** 出现阈值（px） */
  threshold?: number
}>()

const { t } = useI18n()
const visible = ref(false)
let el: HTMLElement | null = null

function onScroll() {
  const top = el ? el.scrollTop : window.scrollY
  visible.value = top > (props.threshold ?? 400)
}

function backTop() {
  if (el) el.scrollTo({ top: 0, behavior: 'smooth' })
  else window.scrollTo({ top: 0, behavior: 'smooth' })
}

function detach() {
  ;(el ?? window).removeEventListener('scroll', onScroll)
  el = null
}

async function attach() {
  detach()
  await nextTick()
  el = props.target ? ((document.querySelector(props.target) as HTMLElement | null) ?? null) : null
  ;(el ?? window).addEventListener('scroll', onScroll, { passive: true })
  onScroll()
}

watch(() => props.target, () => void attach())

onMounted(() => void attach())
onBeforeUnmount(detach)
</script>

<template>
  <button
    v-show="visible"
    type="button"
    class="hl-backtop"
    :title="t('shell.backtop')"
    @click="backTop"
  >↑</button>
</template>
