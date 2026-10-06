<script setup lang="ts">
/**
 * HlDrawer —— 右侧抽屉（非模态可交互 / 模态遮罩两型）。
 *
 * 0.2.1 新增可拖拽调宽（resizable，默认关，现网行为不变）：
 *  · 抽屉从左缘拖拽条取宽（右缘贴边，左缘是自由边）；宽钳制
 *    [minWidth, viewport×0.95]；
 *  · 给了 storageKey 就持久化到 localStorage——用户拉一次的宽度，
 *    同类抽屉跨会话保持（键名由使用方带 APP_SLUG 前缀）；
 *  · 拖拽走 pointer capture，中途不抢文本选中（全局 user-select 关）。
 */
import { computed, onBeforeUnmount, ref, useAttrs, watch } from 'vue'

import { useI18n } from '@/locales'

const props = withDefaults(
  defineProps<{
    title?: string
    width?: string
    /** 点击遮罩关闭（仅 modal=true 时生效） */
    maskClosable?: boolean
    /** 是否模态（有遮罩+锁滚动），默认 true；非模态时底层可交互 */
    modal?: boolean
    /** 是否显示默认头部，默认 true；设为 false 时由 slot 完全自定义内容 */
    withHeader?: boolean
    /** 顶部偏移像素（如避开 sticky 导航栏），默认 0 */
    top?: number
    /** 可拖拽调宽（左缘拖拽条）；开启时 width 作为初始宽度 */
    resizable?: boolean
    /** 拖宽持久化 localStorage 键（配合 resizable；缺省不持久化） */
    storageKey?: string
    /** 拖拽最小宽（px） */
    minWidth?: number
  }>(),
  { title: '', width: '250px', maskClosable: true, modal: true, withHeader: true, top: 0,
    resizable: false, storageKey: '', minWidth: 320 },
)

const attrs = useAttrs()
const model = defineModel<boolean>({ default: false })
const { t } = useI18n()

/* 初始宽：localStorage 偏好 > width prop；解析失败回 prop（NaN/越界钳制兜底） */
function initialWidth(): number {
  const fallback = parseInt(props.width, 10) || 360
  if (!props.storageKey) return fallback
  try {
    const saved = parseInt(localStorage.getItem(props.storageKey) || '', 10)
    if (Number.isFinite(saved) && saved >= props.minWidth) return saved
  } catch { /* 隐私模式读失败：用 prop 值 */ }
  return fallback
}
const liveWidth = ref(initialWidth())

function clamp(v: number): number {
  const max = Math.floor(window.innerWidth * 0.95)
  return Math.max(props.minWidth, Math.min(max, v))
}

const styleWidth = computed(() =>
  props.resizable ? `${liveWidth.value}px` : props.width,
)

let dragStartX = 0
let dragStartW = 0
let dragging = false

function onPointerDown(e: PointerEvent) {
  if (!props.resizable) return
  dragging = true
  dragStartX = e.clientX
  dragStartW = liveWidth.value
  ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
  document.body.style.userSelect = 'none'
}
function onPointerMove(e: PointerEvent) {
  if (!dragging) return
  // 抽屉贴右边：向左拖 = 变宽
  liveWidth.value = clamp(dragStartW + (dragStartX - e.clientX))
}
function onPointerUp() {
  if (!dragging) return
  dragging = false
  document.body.style.userSelect = ''
  if (props.storageKey) {
    try { localStorage.setItem(props.storageKey, String(liveWidth.value)) } catch { /* 存不了就不存 */ }
  }
}
onBeforeUnmount(() => {
  document.body.style.userSelect = ''
})

// 仅模态时锁定 body 滚动；非模态不锁
watch(model, (v) => {
  if (props.modal) {
    document.body.style.overflow = v ? 'hidden' : ''
  }
  // 重开时回读持久化宽度（可能在别的实例里被拖过）
  if (v && props.resizable && props.storageKey) liveWidth.value = initialWidth()
})

defineOptions({ inheritAttrs: false })
</script>

<template>
  <Teleport to="body">
    <Transition name="hl-drawer-fade" appear>
      <div v-if="model && modal" class="hl-drawer-mask" @click="maskClosable && (model = false)" />
    </Transition>
    <Transition name="hl-drawer-slide" appear>
      <div
        v-if="model"
        class="hl-drawer"
        :class="[attrs.class, { 'hl-drawer--non-modal': !modal }]"
        :style="[
          attrs.style as Record<string, string>,
          {
            width: styleWidth,
            top: top ? top + 'px' : undefined,
            height: top ? `calc(100% - ${top}px)` : undefined,
          },
        ]"
      >
        <!-- 左缘拖宽条（resizable 时渲染）：pointer capture 拖拽 -->
        <div
          v-if="resizable"
          class="hl-drawer__resizer"
          role="separator"
          aria-orientation="vertical"
          :title="t('shell.drawer.resize')"
          @pointerdown="onPointerDown"
          @pointermove="onPointerMove"
          @pointerup="onPointerUp"
          @pointercancel="onPointerUp"
        ></div>
        <div v-if="withHeader" class="hl-drawer__head">
          {{ title }}
          <button type="button" class="hl-dialog__close" @click="model = false">✕</button>
        </div>
        <div class="hl-drawer__body" :class="{ 'hl-drawer__body--full': !withHeader }">
          <slot />
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* 左缘拖宽条：6px 命中带，悬停亮强调色竖线提示可拖 */
.hl-drawer__resizer {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 6px;
  cursor: col-resize;
  z-index: 5;
  touch-action: none;
}
.hl-drawer__resizer::after {
  content: '';
  position: absolute;
  left: 2px;
  top: 50%;
  width: 2px;
  height: 40px;
  transform: translateY(-50%);
  border-radius: 2px;
  background: var(--border);
  transition: background 0.15s, height 0.15s;
}
.hl-drawer__resizer:hover::after,
.hl-drawer__resizer:active::after {
  background: var(--accent);
  height: 72px;
}
</style>
