/* 实验性功能开关（0.5.0）：设置页风险确认后开启，侧栏 Steampy 实验页按此渲染。
   模块级单例：App 外壳与设置页共享同一份状态，load 幂等（本会话只拉一次，
   设置页改动后 force 刷新）。 */
import { ref } from 'vue'

import { settingsApi } from '@/api/client'

const enabled = ref(false)
let loaded = false

export function useExperimental() {
  async function load(force = false): Promise<boolean> {
    if (loaded && !force) return enabled.value
    try {
      enabled.value = (await settingsApi.getExperimental()).enabled
      loaded = true
    } catch {
      /* 读取失败保持默认关（侧栏不显示实验页，设置页内再重试） */
    }
    return enabled.value
  }

  async function set(next: boolean): Promise<boolean> {
    enabled.value = (await settingsApi.updateExperimental(next)).enabled
    loaded = true
    return enabled.value
  }

  return { enabled, load, set }
}
