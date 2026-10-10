/* English · steampy (mirrors zh-CN/steampy.ts, keys must align 1:1). */

import type { MessageKey } from '../zh-CN'

const steampy: Partial<Record<MessageKey, string>> = {
  'steampy.title': 'Steampy Market',
  'steampy.badge': 'Experimental',
  'steampy.desc':
    'Public CDKey market data from Steampy, proxied as-is: lowest CDK listing price, on-sale count, and Steam price/history-low per game. Third-party public data for reference only — not purchase advice. Click a card to visit the site.',
  'steampy.searchPlaceholder': 'Filter by name / AppID (current page)',
  'steampy.gated': 'Experimental features are off: confirm the risks under [Settings → Experimental] to show this page in the sidebar.',
  'steampy.goSettings': 'Open settings',
  'steampy.loadFailed': 'Market fetch failed (the site may be rate-limiting or changed) — try again later',
  'steampy.empty': 'No matching games on this page',
  'steampy.onSale': '{n} on sale',
  'steampy.lowest': 'History low {p}',
}

export default steampy
