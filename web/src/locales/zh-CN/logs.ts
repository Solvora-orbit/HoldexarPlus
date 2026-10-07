/* logs 词条 —— 运行日志页（views/logs/Index.vue）。

   「已复制」直接复用 common.copied（与按钮态同一件事），故此处没有该条。
   正文复制走系统原生拖选 + Ctrl+C，不需要词条。
   行结构着色（`时间 [级别] logger: 消息`）是后端原样字符串；0.3.0 起
   前端拆段呈现，模块中文名见下方 logs.mod.* 词条（动态拼 key 查表）。 */

const logs = {
  /* SSE 连接状态 */
  'logs.stream.connected': '实时流已连接',
  'logs.stream.connecting': '连接中…（断线自动重连）',

  /* 工具行计数（中英语序不同：中文量词在后，英文复数在后） */
  'logs.count.lines': '{n} 行',
  'logs.count.errors': '错误 {n}',
  'logs.count.warnings': '警告 {n}',

  /* 工具行按钮 */
  'logs.action.resume': '回到底部并跟随',
  'logs.action.following': '跟随中',
  'logs.action.copyAll': '复制全部 {n} 行',
  'logs.action.clear': '清屏',

  /* 日志框 */
  'logs.empty': '暂无日志输出——后台爬取/汇率刷新/调度器运行时将实时打印到这里',

/* 复制结果提示 */
  'logs.copy.failedHint': '复制失败——请在日志框内手动拖选后 Ctrl+C',

  /* ── 模块名人话化（0.3.0）：logger 前缀 → 中文模块标签（logs/Index.vue 动态拼 key） ── */
  'logs.mod.app': '应用',
  'logs.mod.app.main': '启动',
  'logs.mod.app.crawler': '爬虫',
  'logs.mod.app.core.scheduler': '定时任务',
  'logs.mod.app.core.database': '数据库',
  'logs.mod.app.core.updater': '自动更新',
  'logs.mod.app.core.events': '事件通道',
  'logs.mod.app.core.backup': '备份',
  'logs.mod.app.core.seed_assets': '种子资产',
  'logs.mod.app.core.orchestration': '编排',
  'logs.mod.app.core.config': '配置',
  'logs.mod.app.core.logging': '日志',
  'logs.mod.app.core.keyring': '密钥保管',
  'logs.mod.app.core.paths': '路径',
  'logs.mod.app.domains.proxypool': '代理池',
  'logs.mod.app.domains.proxies': '网络',
  'logs.mod.app.domains.pilot': '领航员',
  'logs.mod.app.domains.agent': '代理运行器',
  'logs.mod.app.domains.wishlist': '愿望单',
  'logs.mod.app.domains.crawl': '自动抓取',
  'logs.mod.app.domains.games': '游戏库',
  'logs.mod.app.domains.rates': '汇率',
  'logs.mod.app.domains.alerts': '降价提醒',
  'logs.mod.app.domains.humble': 'HB 捆绑包',
  'logs.mod.app.domains.metadata': '平台数据',
  'logs.mod.app.domains.account': '账号',
  'logs.mod.app.domains.monitoring': '监控池',
  'logs.mod.app.domains.steam_events': 'Steam 活动',
  'logs.mod.app.domains.achievements': '成就',
  'logs.mod.app.domains.family': '家庭库',
  'logs.mod.app.domains.bills': '账单',
  'logs.mod.app.domains.redeem': '兑换码',
  'logs.mod.app.domains.regions': '区服',
  'logs.mod.app.domains.settings': '设置',
  'logs.mod.app.domains.system': '系统',
  'logs.mod.app.domains.notifications': '通知',
  'logs.mod.desktop': '桌面壳',
  'logs.mod.run': '启动器',
} as const

export default logs
