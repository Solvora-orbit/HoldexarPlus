/* priceevents 词条 —— views/priceevents/Index.vue（降价动态子页，plus.3）。

   仪表盘降价动态的完整列表：flag=any（新史低/平史低/永降/折扣中）按变动
   排序，可切「仅新史低」（flag=hl）与折扣力度排序。 */

const priceevents = {
  'priceEvents.desc':
    '全库降价动态：新史低 / 平史低 / 永降 / 折扣中。仪表盘只展示精选，这里是完整列表。',
  'priceEvents.filter.all': '全部',
  'priceEvents.filter.newLow': '仅新史低',
  'priceEvents.sort.updated': '最近变动',
  'priceEvents.sort.discount': '折扣力度',
  'priceEvents.empty': '还没有降价动态：跑一轮抓取后回来。',
}

export default priceevents
