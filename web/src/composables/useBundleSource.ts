/* 捆绑包站源面板共享逻辑（0.4.0）：HB / Fanatical / GMG 三面板共用。
 *
 * 从 HumbleBundlesPanel 抽取：加载/轮询单飞（5s）/收录触发/列表收放/
 * 档期倒计时/抽屉打开（seq token 防竞态——快速连点两包旧详情不得覆盖新详情，
 * HlGameCard/PriceTrendDrawer 同款先例）。API 适配器 = humble/fanatical/gmg
 * 三个 client 的同构五件套（bundles/bundleDetail/seen/refresh/ingestNow）。
 */
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue'

import type {
  GameListItem,
  HumbleBundleItem,
  HumblePendingGame,
  StoreBundleDetailPayload,
  StoreBundleItem,
  StoreTier,
} from '@/api/client'
import { useI18n } from '@/locales'
import { message } from '@/components/ui'

/** 三个站源 client 的同构面（humble 也满足此形状） */
export interface BundleSourceApi {
  bundles: () => Promise<{ running: boolean; ingestRunning?: boolean; ingestPending?: number; bundles: (HumbleBundleItem | StoreBundleItem)[] }>
  bundleDetail: (slug: string) => Promise<StoreBundleDetailPayload | (HumbleBundleItem & Record<string, unknown>)>
  seen: (slug: string) => Promise<{ ok: boolean }>
  refresh: () => Promise<{ ok: boolean; started: boolean; running: boolean }>
  ingestNow: () => Promise<{ ok: boolean; started: boolean; running: boolean }>
  reset: (slug: string) => Promise<{ ok: boolean; started: boolean }>
}

export function useBundleSource(
  api: BundleSourceApi,
  msgPrefix: string,
  opts?: { mapTiers?: (payload: Record<string, unknown>) => StoreTier[] },
) {
  const { t } = useI18n()

  const bundles = ref<(HumbleBundleItem | StoreBundleItem)[]>([])
  const loading = ref(true)
  const running = ref(false)
  const ingestRunning = ref(false)
  const ingestPending = ref(0)

  async function load() {
    try {
      const res = await api.bundles()
      bundles.value = res.bundles
      running.value = res.running
      ingestRunning.value = res.ingestRunning ?? false
      ingestPending.value = res.ingestPending ?? 0
      if (res.running || res.ingestRunning) schedulePoll()
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      loading.value = false
    }
  }

  async function ingestNow() {
    if (ingestRunning.value) return
    try {
      const r = await api.ingestNow()
      if (r.ok && (r.started || r.running)) {
        ingestRunning.value = true
        message.info(t(`${msgPrefix}.ingestStarted`))
        schedulePoll()
      }
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }

  let pollTimer: number | null = null

  function schedulePoll() {
    if (pollTimer != null) return
    pollTimer = window.setInterval(async () => {
      try {
        const res = await api.bundles()
        const wasRefresh = running.value
        const wasIngest = ingestRunning.value
        running.value = res.running
        ingestRunning.value = res.ingestRunning ?? false
        ingestPending.value = res.ingestPending ?? 0
        bundles.value = res.bundles
        if (!res.running && !res.ingestRunning) {
          stopPoll()
          if (wasIngest) message.success(t(`${msgPrefix}.ingestDone`))
          else if (wasRefresh) message.success(t(`${msgPrefix}.refreshDone`))
        }
      } catch {
        /* 轮询失败等下一轮 */
      }
    }, 5000)
  }

  function stopPoll() {
    if (pollTimer != null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  async function refresh() {
    try {
      const r = await api.refresh()
      if (r.ok) {
        running.value = true
        message.info(t(`${msgPrefix}.refreshStarted`))
        schedulePoll()
      }
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }

  onMounted(() => void load())
  onActivated(() => {
    void load()
  })
  onDeactivated(stopPoll)
  onBeforeUnmount(stopPoll)

  // ─── 列表收放：默认前 8（0.2.1 用户约定），其余「展开全部」 ───
  const PREVIEW = 8
  const expanded = ref(false)
  const shown = computed(() => (expanded.value ? bundles.value : bundles.value.slice(0, PREVIEW)))
  const hiddenCount = computed(() => Math.max(0, bundles.value.length - PREVIEW))

  // ─── 档期文案与倒计时三档（0.2.2）───
  function period(b: HumbleBundleItem | StoreBundleItem): string {
    const f = (iso: string) => iso.slice(5, 10).replace('-', '.')
    if (b.startAt && b.endAt) return `${f(b.startAt)} ~ ${f(b.endAt)}`
    if (b.endAt) return `~ ${f(b.endAt)}`
    if (b.startAt) return `${f(b.startAt)} ~`
    return ''
  }

  function daysLeft(endAt: string | null): number | null {
    if (!endAt) return null
    const ms = new Date(endAt).getTime() - Date.now()
    return ms > 0 ? Math.max(1, Math.ceil(ms / 86400000)) : 0
  }

  /** 远期占位到期（>1 年，站点「永久在售」形态）不显示倒计时徽章（0.5.0） */
  function isLongRunning(endAt: string | null): boolean {
    const d = daysLeft(endAt)
    return d !== null && d > 365
  }

  function urgencyClass(endAt: string | null): string {
    const d = daysLeft(endAt)
    if (d === null) return ''
    if (d <= 3) return 'is-hot'
    if (d <= 14) return 'is-warn'
    return 'is-cool'
  }

  // ─── 包详情抽屉（seq token 防竞态：旧响应不得覆盖新开的包）───
  const drawerWidth = '980px'
  const open = ref(false)
  const detailLoading = ref(false)
  const detail = ref<HumbleBundleItem | StoreBundleItem | null>(null)
  const detailItems = ref<GameListItem[]>([])
  const detailTotal = ref(0)
  const detailPending = ref<HumblePendingGame[]>([])
  const detailTiers = ref<StoreTier[]>([])
  /** 站点合辑成员卡片集（0.6.0；与 items 分开——成员非顶层条目） */
  const detailMemberItems = ref<GameListItem[]>([])

  const cardByAppid = computed(() => {
    const m = new Map<number, GameListItem>()
    for (const g of detailItems.value) m.set(g.appid, g)
    return m
  })

  /** 非卡片条目状态 → 文案键（三态：关联中/收录中/未找到） */
  function humStatusKey(status: HumblePendingGame['status']): string {
    if (status === 'ingesting') return 'bundles.store.ingesting'
    if (status === 'not_found') return 'bundles.store.notFound'
    return 'bundles.store.resolving'
  }

  /** 档位组里没卡片的游戏行的状态文案键 */
  function rowStatusKey(title: string, appid: number | null): string {
    const p = detailPending.value.find((x) => x.title === title)
    if (p) return humStatusKey(p.status)
    return appid == null ? 'bundles.store.resolving' : 'bundles.store.ingesting'
  }

  let detailSeq = 0

  /** 详情载荷落 state（openBundle / 重抓后重载共用；seq 防旧响应覆盖） */
  async function fetchDetailInto(slug: string, seq: number) {
    detailLoading.value = true
    try {
      const res = (await api.bundleDetail(slug)) as unknown as Record<string, unknown>
      if (seq !== detailSeq) return // 抽屉已重开（换包），丢弃旧响应
      detail.value = { ...(detail.value as object), ...(res as object) } as HumbleBundleItem | StoreBundleItem
      detailItems.value = (res.items as GameListItem[]) ?? []
      detailTotal.value = (res.total as number) ?? 0
      detailPending.value = (res.pending as HumblePendingGame[]) ?? []
      detailMemberItems.value = (res.memberItems as GameListItem[]) ?? []
      detailTiers.value = opts?.mapTiers ? opts.mapTiers(res) : ((res.tiers as StoreTier[]) ?? [])
    } catch (e) {
      if (seq === detailSeq) message.error(e instanceof Error ? e.message : String(e))
    } finally {
      if (seq === detailSeq) detailLoading.value = false
    }
  }

  async function openBundle(b: HumbleBundleItem | StoreBundleItem) {
    const seq = ++detailSeq
    detail.value = b
    detailItems.value = []
    detailTotal.value = 0
    detailPending.value = []
    detailTiers.value = []
    detailMemberItems.value = []
    open.value = true
    await fetchDetailInto(b.slug, seq)
    /* 点开即已读：本地先清徽章（即时反馈），再向后端记账 */
    if (b.isNew) {
      b.isNew = false
      try { await api.seen(b.slug) } catch { /* 已读失败不扰：下次点开重试 */ }
    }
  }

  // ─── 单包删数据 + 重抓（0.5.1）：确认气泡在抽屉（HlPopconfirm），这里直接执行；
  //     后台重建（合辑成员解析可达数分钟）——先看清空态，再延迟重载看进度/结果 ───
  const resetting = ref(false)

  async function resetBundle() {
    const slug = detail.value?.slug
    if (!slug || resetting.value) return
    resetting.value = true
    try {
      const r = await api.reset(slug)
      if (r.ok) {
        message.info(t('bundles.store.resetStarted'))
        const seq = ++detailSeq
        detailPending.value = []
        detailTiers.value = []
        detailItems.value = []
        detailMemberItems.value = []
        void fetchDetailInto(slug, seq)
        window.setTimeout(() => { void fetchDetailInto(slug, ++detailSeq) }, 15000)
        window.setTimeout(() => { void fetchDetailInto(slug, ++detailSeq) }, 45000)
      }
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      resetting.value = false
    }
  }

  return {
    // 状态
    bundles, loading, running, ingestRunning, ingestPending,
    expanded, shown, hiddenCount,
    open, detailLoading, detail, detailItems, detailTotal, detailPending, detailTiers,
    detailMemberItems,
    cardByAppid, drawerWidth, resetting,
    // 动作
    load, refresh, ingestNow, openBundle, resetBundle, stopPoll,
    // 展示助手
    period, daysLeft, isLongRunning, urgencyClass, humStatusKey, rowStatusKey,
  }
}
