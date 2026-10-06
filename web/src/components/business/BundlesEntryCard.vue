<script setup lang="ts">
/* 捆绑包中心轻入口（仪表盘）：当月包期名 + 款数一行摘要，整卡点击进
   /bundles 捆绑包中心。plus.3 起仪表盘不再放完整 HB 当月包卡（组件保留在
   components/business/HbChoiceCards.vue），完整功能（近一年进包记录、
   多站源、Steam 捆绑包导入）都在中心页——仪表盘只留一张轻入口防臃肿。 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { metadataApi, type HbChoiceOffersPayload } from '@/api/client'
import { useI18n } from '@/locales'
import HlButton from '@/components/ui/HlButton.vue'
import HlIcon from '@/components/ui/HlIcon.vue'

const { t } = useI18n()
const router = useRouter()
const offers = ref<HbChoiceOffersPayload | null>(null)

onMounted(async () => {
  // 入口卡拿不到数据就显示通用引导，不打错误态（仪表盘不该为入口卡报错）
  try {
    offers.value = await metadataApi.hbChoiceOffers()
  } catch {
    offers.value = null
  }
})

const summary = computed(() => {
  if (offers.value?.ok) {
    return t('dashboard.bundles.entrySummary', {
      label: offers.value.label,
      n: offers.value.games.length,
    })
  }
  return t('dashboard.bundles.entryEmpty')
})
</script>

<template>
  <div
    class="card bundles-entry"
    data-section="dashboard.section.bundles"
    role="button"
    tabindex="0"
    @click="router.push('/bundles')"
    @keydown.enter="router.push('/bundles')"
  >
    <div class="bundles-entry__head">
      <div class="bundles-entry__title">
        <HlIcon name="gift" :size="16" />
        {{ t('dashboard.bundles.entryTitle') }}
      </div>
      <HlButton variant="text" size="sm">{{ t('dashboard.bundles.entry') }} →</HlButton>
    </div>
    <p class="bundles-entry__summary">{{ summary }}</p>
  </div>
</template>

<style scoped>
.bundles-entry {
  padding: 14px 18px;
  cursor: pointer;
  transition: border-color var(--transition), box-shadow var(--transition);
}
.bundles-entry:hover {
  border-color: var(--accent-a50);
  box-shadow: var(--shadow-lg);
}
.bundles-entry__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.bundles-entry__title {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text-primary);
}
.bundles-entry__summary {
  margin-top: 6px;
  font-size: 12.5px;
  color: var(--text-secondary);
}
</style>
