/* English 词典 · logs（与 zh-CN/logs.ts 同构，key 必须逐一对齐）。

   「已复制」按钮态复用 common.copied，此处不重复。 */

import type { MessageKey } from '../zh-CN'

const logs: Partial<Record<MessageKey, string>> = {
  /* SSE connection state */
  'logs.stream.connected': 'Live stream connected',
  'logs.stream.connecting': 'Connecting… (reconnects automatically)',

  /* Toolbar counters */
  'logs.count.lines': '{n} lines',
  'logs.count.errors': '{n} errors',
  'logs.count.warnings': '{n} warnings',

  /* Toolbar buttons */
  'logs.action.resume': 'Resume following',
  'logs.action.following': 'Following',
  'logs.action.copyAll': 'Copy all {n} lines',
  'logs.action.clear': 'Clear',

  /* Log box */
  'logs.empty': 'No log output yet — crawls, rate refreshes and the scheduler print here in real time',

  /* Copy result toasts */
  'logs.copy.failedHint': 'Copy failed — select the text manually and press Ctrl+C',

  /* Module-name humanization (0.3.0): logger prefix → module label chip */
  'logs.mod.app': 'App',
  'logs.mod.app.main': 'Startup',
  'logs.mod.app.crawler': 'Crawler',
  'logs.mod.app.core.scheduler': 'Scheduler',
  'logs.mod.app.core.database': 'Database',
  'logs.mod.app.core.updater': 'Updater',
  'logs.mod.app.core.events': 'Event bus',
  'logs.mod.app.core.backup': 'Backup',
  'logs.mod.app.core.seed_assets': 'Seed assets',
  'logs.mod.app.core.orchestration': 'Orchestration',
  'logs.mod.app.core.config': 'Config',
  'logs.mod.app.core.logging': 'Logging',
  'logs.mod.app.core.keyring': 'Secrets',
  'logs.mod.app.core.paths': 'Paths',
  'logs.mod.app.domains.proxypool': 'Proxy pool',
  'logs.mod.app.domains.proxies': 'Network',
  'logs.mod.app.domains.pilot': 'Pilot',
  'logs.mod.app.domains.agent': 'Agent runtime',
  'logs.mod.app.domains.wishlist': 'Wishlist',
  'logs.mod.app.domains.crawl': 'Auto crawl',
  'logs.mod.app.domains.games': 'Game library',
  'logs.mod.app.domains.rates': 'Exchange rates',
  'logs.mod.app.domains.alerts': 'Price alerts',
  'logs.mod.app.domains.humble': 'HB bundles',
  'logs.mod.app.domains.metadata': 'Store data',
  'logs.mod.app.domains.account': 'Account',
  'logs.mod.app.domains.monitoring': 'Watch pool',
  'logs.mod.app.domains.steam_events': 'Steam events',
  'logs.mod.app.domains.achievements': 'Achievements',
  'logs.mod.app.domains.family': 'Family library',
  'logs.mod.app.domains.bills': 'Bills',
  'logs.mod.app.domains.redeem': 'Redeem codes',
  'logs.mod.app.domains.regions': 'Regions',
  'logs.mod.app.domains.settings': 'Settings',
  'logs.mod.app.domains.system': 'System',
  'logs.mod.app.domains.notifications': 'Notifications',
  'logs.mod.desktop': 'Desktop shell',
  'logs.mod.run': 'Launcher',
}

export default logs
