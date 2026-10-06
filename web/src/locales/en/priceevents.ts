/* English 词典 · priceevents（与 zh-CN/priceevents.ts 同构，key 必须逐一对齐）。 */

import type { MessageKey } from '../zh-CN'

const priceevents: Partial<Record<MessageKey, string>> = {
  'priceEvents.desc':
    'Price moves across the store: new lows, ties, permanent drops and discounts. The dashboard shows a curated slice — this is the full list.',
  'priceEvents.filter.all': 'All',
  'priceEvents.filter.newLow': 'New lows only',
  'priceEvents.sort.updated': 'Recently moved',
  'priceEvents.sort.discount': 'Biggest discount',
  'priceEvents.empty': 'No price moves yet — run a crawl and come back.',
}

export default priceevents
