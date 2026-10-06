import { useRouter } from 'vue-router'

/**
 * 来源感知返回（子页面/详情页统一出口）：
 * 有浏览历史就 `router.back()`（保留上一页的滚动与筛选状态），
 * 直接落地 / 刷新进入时退回 fallback 路径。
 * 先例：game-detail 的 goBack（plus.2）。子页面一律用它，不要手写
 * `router.push('/xxx')` 冒充返回（那会把「从哪来回哪去」变成写死跳转）。
 */
export function useGoBack(fallback: string) {
  const router = useRouter()
  function goBack() {
    if (window.history.state?.back != null) router.back()
    else void router.push(fallback)
  }
  return { goBack }
}
