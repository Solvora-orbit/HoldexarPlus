/* Steampy 行情实验页（views/steampy/Index.vue，0.5.0）。

   数据口径：站点公开 CDK 行情透传（后端 /steampy/hot 代理，无账号只读），
   人民币计价；keyPrice = 最低 CDK 挂单价。实验数据不作购买建议。 */

const steampy = {
  'steampy.title': 'Steampy 行情',
  'steampy.badge': '实验',
  'steampy.desc':
    'Steampy（游戏 CDK 市场）公开行情透传：各游戏的最低 CDK 挂单价、在售数量与 Steam 原价/史低对照。数据为第三方站点公开页面，仅供参考、不作购买建议；点击卡片可前往站点查看。',
  'steampy.searchPlaceholder': '按游戏名 / AppID 过滤（当前页）',
  'steampy.gated': '实验性功能未开启：到【设置 → 实验性功能】确认风险后开启，侧栏才会显示本页。',
  'steampy.goSettings': '前往设置',
  'steampy.loadFailed': '行情抓取失败（站点可能限速或改版），稍后再试',
  'steampy.empty': '当前页没有匹配的游戏',
  'steampy.onSale': '{n} 个在售',
  'steampy.lowest': '史低 {p}',
} as const

export default steampy
