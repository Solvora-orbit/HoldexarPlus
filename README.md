<div align="center">

<br>

# HoldexarPlus

**本地 Steam 多区价格监控终端** —— Holdexar 独立分支 · 持续演进版

[![Version](https://img.shields.io/badge/version-v0.1.1--plus.3-orange)](../../releases)
[![Python 3.13+](https://img.shields.io/badge/Python-3.13%2B-blue?logo=python&logoColor=white)](https://www.python.org)
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Vue 3](https://img.shields.io/badge/Frontend-Vue%203-42b883?logo=vuedotjs&logoColor=white)](https://vuejs.org)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D4?logo=windows&logoColor=white)](https://github.com)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

</div>

---

> [!NOTE]
> **HoldexarPlus** 基于 [**GLrone/Holdexar**](https://github.com/GLrone/Holdexar) 创建（GPL-3.0），由 [@Solvora-orbit](https://github.com/Solvora-orbit) 独立维护，与原项目无隶属关系，后续改动不代表原项目。原项目版权归 [GLrone](https://github.com/GLrone)，感谢原作者的贡献。

## HoldexarPlus 与原项目有什么不同

一句话：底座是原项目的，**方向是使用者体验**——原项目偏功能广度，Plus 版围绕「装好就能用、用了不迷路、更新不受制于人」持续打磨。两者数据目录与库文件完全兼容，原版用户迁移零成本。

| 维度 | 原项目 Holdexar | HoldexarPlus（当前） |
|---|---|---|
| **更新通道** | 跟随上游发版 | **完全独立**：应用内「检查更新」只读本仓库发布，上游更新不影响你 |
| **直连体验** | 「直连优先」仍要求代理池出口，空池报「没有可用出口」 | 直连 / 直连优先**统一为本机网络托管**，无代理也能完整使用全部功能 |
| **策略可审计** | 策略变化无痕 | 每次路由策略变更**写入日志**，设置跳变一查便知 |
| **抓取方式** | 任务粒度较粗 | **任务页可选方案**：全部 / 愿望单 / 折扣中游戏 / 已购库 / 监控池 / 指定 AppID + 特惠榜一键抓取 |
| **榜单可见性** | 特惠榜抓完「看不见摸不着」 | **独立榜单视图**：特惠 / 热销 / 热门新品 / 即将推出四榜可翻，抓完即达 |
| **自助排障** | 需翻文档问作者 | **帮助与诊断卡**：FAQ、当前策略、版本号、**数据目录路径**一屏直达；日志页一键跳转 |
| **数据管理** | 手动删文件 | 应用内**一键清空本地数据**（双保险确认），重置即全新安装 |
| **外观** | 深浅两档 | 深浅之外**四套主题色**（蒸汽蓝/翠绿/暖橙/紫罗兰），偏好**跨启动持久** |
| **细节** | — | 游戏库缺价一键补抓、页面跟随爬取完成自动刷新、返回按来源导航 |

## 更新路线（Roadmap）

**已发布** ✅

- **v0.1.1-plus.1**（2026-10-06）：fork 基线切割（更新源/品牌/版本号）· 直连形态修复 + 策略审计 · 多抓取方案 · 帮助诊断卡 + 数据清空 · 主题色方案 · 按钮体系统一
- **v0.1.1-plus.2**（2026-10-06）：偏好持久化修复（主题不再跳回默认）· 榜单视图上线 · 游戏库补抓无价格 + 数据自动同步 · 返回键来源感知 · Steam 账号管理收敛至设置页
- **v0.1.1-plus.3**（2026-10-07）：捆绑包中心（HB 月包 + 近一年进包记录 + 多站源预留）· HB 历史逐月补抓 · 降价动态独立子页 · 榜单筛选与竞态修复 · 统计卡跳转 · 游戏库家庭成员计数修复 · 网络页防闪控制台 · 图标重绘 H+ PLUS · 侧栏指示条增强

**规划中**

- [ ] 顶栏主题色快捷切换（不止设置页入口）
- [ ] 「自动抓取」与「任务」两页的引导互链，自动化控制不再分散
- [ ] 公共目录库模板随发布分发——全新安装首次进店即可看到完整目录，冷启动等待归零
- [ ] 榜单视图增强：游戏卡 TOP 徽章、榜内价格变化高亮
- [ ] 自有 Scoop 渠道（当前暂沿用上游 bucket）
- [ ] 价格事件摘要邮件（每日/每周汇总，替代逐条轰炸）

> 想投票或催更？直接在 [Issues](../../issues) 里说。合理的功能请求优先安排。

---

# 使用手册（写给玩家）

## 这是什么？

你在 Steam 上买游戏时可能遇到过：同一个游戏，阿根廷区、土耳其区、国区的价格差好几倍。**HoldexarPlus 就是帮你把这件事自动盯起来的工具**——

- 你只需要告诉它「我想盯哪些游戏」：粘贴游戏链接、登录 Steam 同步愿望单、或者导入一份列表；
- 剩下的全由它自己来：每隔几小时把各区价格抓回来、按实时汇率折算成人民币、发现降价和历史新低；
- 价格降到你的心理价位时，它自动发邮件提醒你。

它是一个**绿色软件**：解压就能用，不需要安装、不需要注册账号、不需要懂任何技术。所有数据都存在你自己的电脑里，不连接任何云端服务器，也没有任何数据上报。没有代理也能用：路由策略选「直连」即可全程走本机网络。

## 三步上手

### 第 1 步：下载并打开

1. 到 [Releases](../../releases) 页面下载 `HoldexarPlus-win64-v<版本>.zip`；
2. 解压到任意文件夹，双击 `HoldexarPlus/HoldexarPlus.exe`；
3. 第一次打开会弹出安全提示（未签名程序的正常现象，不是病毒），点「更多信息 → 仍要运行」即可，详见下方[安全提示](#首次运行安全提示)。

打开后会有一遍新手导览，跟着「下一步」走就行，随时可以跳过。

### 第 2 步：告诉它你想盯哪些游戏

三种方式任选，可以混着用：

- **粘贴链接**：在【仪表盘】的添加框里粘贴任意 Steam 商店链接（比如 `https://store.steampowered.com/app/105600/`），回车即可；
- **登录 Steam 同步**：到【设置】绑定 Steam 账号（支持账号密码直接登录，遇到手机验证码按提示输入即可），愿望单和已购游戏会自动进来；
- **从游戏卡片关注**：逛【找游戏】时，看到想长期盯价的游戏，点卡片右上角的**星标**即可。

### 第 3 步：设一条降价提醒

到【价格提醒】页，建一条规则，比如「泰拉瑞亚 国区 ≤ ¥42 时通知我」。之后每当价格刷新，符合条件就会自动发邮件到你预留的邮箱。

到这里核心用法就完成了。**接下来你什么都不用做**——程序会自己定时更新价格、自己发现降价、自己发提醒。你随时打开看最新数据就行。

## 它能帮我做什么？

| 功能 | 说明（人话版） |
|---|---|
| 找游戏 | 41 个国家/地区的价格一屏对比：当地标价、折扣、折算人民币、史低标记；可按折扣和锁区筛选；工具条直达**榜单视图** |
| 降价动态 | 全库新史低 / 平史低 / 永降 / 折扣中的完整动态列表，仪表盘只放精选 |
| 游戏详情 | 同一游戏的标准版/豪华版等多版本比价；每个区的历史价格走势曲线 |
| 游戏库 | 账号已购 + 家庭库 + 游玩动态聚合分析；缺价游戏一键补抓 |
| 价格提醒 | 降价、历史新低自动发邮件；阈值条件自己定 |
| 愿望单与已购 | 绑定 Steam 后自动同步，不用手动维护 |
| 账单 | Steam 消费记录同步，多币种按当日汇率折算成人民币 |
| 捆绑包 / 免费游戏 | **捆绑包中心**：HB 月包当月内容 + 近一年进包记录（包内游戏对应 Steam，点击看各区价格）、Steam 捆绑包链接导入；Epic 喜加一、Steam 免费游戏动态，换新自动提示 |
| 活动日历 | Steam 官方大促（夏促/冬促）和新品游戏节的时间表与倒计时 |
| 汇率 | 39 种货币兑人民币走势，自带 2010 年至今的历史档案；卡商充值价目参考 |
| 任务 | 想立刻刷新哪批数据就抓哪批：全部 / 愿望单 / 折扣中 / 已购库 / 监控池 / 特惠榜 |
| 工具箱 | 礼品卡 CDK 批量激活，逐码出回执 |

## 灵动岛：变化会自己来找你

窗口顶部有一颗「灵动岛」胶囊，程序里**值得你知道的事**都会在那里冒出来，不需要你主动翻页面：

- 价格更新到哪了（正在更新 / 已更新 / 部分暂缺）；
- Humble Choice 月包换新了、Epic 喜加一轮换了；
- 有新版本可以更新了。

点开胶囊能看详情和进度，不需要知道任何内部机制。

## 首次运行安全提示

> [!IMPORTANT]
> 本应用发布包**未经代码签名**，首次运行会遇到两类安全提示，均为无签名程序的正常现象，不是病毒：
>
> 1. **Windows SmartScreen 弹窗**（「Windows 已保护你的电脑」）：点 **更多信息 → 仍要运行** 即可。
> 2. **杀毒软件报毒 / 主动防御弹窗**（如「正在创建 BITS 任务」）：选 **允许 / 信任**，不要阻止——阻止可能影响窗口渲染。建议把整个程序目录加入杀软白名单。
>
> 请只从官方 [Releases](../../releases) 页下载，并用页内公布的 SHA256 校验安装包。

## 常见问题（FAQ）

<details>
<summary>首次运行弹出「Windows 已保护你的电脑」？</summary>

Windows 对未签名程序的标准提示，与病毒无关。点 **更多信息 → 仍要运行** 即可，同一版本之后不会再弹。
</details>

<details>
<summary>杀毒软件报毒？</summary>

无签名程序常见误报；随包分发的代理内核（开源的 mihomo，见文末致谢）也常被标记。处置：把整个程序目录加入白名单，弹窗选「允许」。
</details>

<details>
<summary>没有代理能用吗？</summary>

能。【网络】页把路由策略设为「直连」或「直连优先」，全部功能托管到你本机网络环境（有加速器/本地代理软件时它们的通道就是实际出口）；限速由程序统一约束，不会打爆你的网络。想更稳的全区抓取再接入自备 Clash 订阅。
</details>

<details>
<summary>改了主题/语言，重启后又变回默认？</summary>

v0.1.1-plus.2 起已修复（桌面壳持久化存储）。如果你用的是更早的包，升级即可；设置跳变类问题可在【设置 → 帮助与诊断】看数据目录，或到日志页搜「[策略]」审计记录。
</details>

<details>
<summary>登录 Steam 时收不到验证码 / 登录不上？</summary>

应用内登录走 Steam 官方认证接口。验证码分两种：邮箱验证码去邮箱收；手机令牌打开 Steam 手机 App 就能看到。输错可以重新输入；如果开启了手机 App 确认，直接在手机上点「确认」即可，程序会自动继续。密码只在本机内存里用一次，不会保存。
</details>

<details>
<summary>刚装好，部分游戏没有价格？</summary>

新加的游戏会尽快补齐首趟价格（你星标关注的排最前）；刚装好时的批量数据要逐步积累，等一轮更新周期走完就好。也可以手动触发：游戏库页脚「补抓无价格游戏」一键定向补齐，或【任务】页选方案立即开抓。
</details>

<details>
<summary>价格和 Steam 商店页不一致？</summary>

价格每 6 小时自动刷新一轮，两轮之间的临时调价有滞后；每条价格都带抓取时间，卡片上能看到数据的新旧。
</details>

<details>
<summary>我的账号密码安全吗？数据存在哪？</summary>

账号密码登录走 Steam 官方接口，密码只在登录那一刻在内存里用一次，**不落盘、不写日志**；登录成功后的凭据加密存放在本机数据库里。全部数据（`holdexar.db`）都在你自己的电脑上（默认 `%LOCALAPPDATA%\holdexar`，便携模式在程序目录 `data/`）。零云端、零遥测——除了 Steam 官方接口和你自备的代理订阅，不连接任何第三方服务。
</details>

<details>
<summary>不想让它自动抓价格？</summary>

【自动抓取】页关闭「自动价格更新」即可：定时刷新和自动重试会停，手动抓取不受影响，随时能再打开。
</details>

<details>
<summary>想从原版 Holdexar 迁移？</summary>

不用迁。Plus 版数据目录与库文件同原版（`holdexar.db`），直接退出原版、解压 Plus 版启动即可无损接管全部游戏、账号绑定与价格历史。建议之后卸载原版避免双实例。
</details>

## 更新与升级

- 有新版本时启动会主动提示一次，侧栏「我」项常驻红点，顶栏出现更新胶囊（发现新版 / 下载中 / 待重启）；进【我 → 应用更新】一键升级。
- Plus 版只跟随**本仓库**的发布——上游原项目更新不会推送给你，也不会覆盖你的版本。
- 换装只替换程序文件，用户数据、账号绑定、价格历史一概不动；换装中断会自动回滚到原版本并重新打开应用。
- 也可以手动下载新版解压覆盖，数据不受影响。

**Scoop 渠道**（便携应用，不写注册表、不装服务；Plus 暂沿用上游 bucket，自有渠道建立后切换）：

```powershell
scoop bucket add holdexar https://github.com/GLrone/scoop-bucket
scoop install holdexar
```

每个版本 Release 附带三件资产：应用包 zip（解压即用）、公共数据种子 `holdexar_seed.db`（仅源码运行需要，发布包已内置）、更新清单 `latest.json`（客户端检查更新读取）。

---

# 开发者文档

## 技术架构

| 层 | 组件 |
|---|---|
| 后端 | FastAPI · SQLAlchemy(async) · APScheduler(21 个定时任务) · aiohttp 爬核 |
| 前端 | Vue 3 · TypeScript · Vite · 自制 Hl\* 组件体系(深浅双主题 / 四套主题色 / 中英双语) |
| 桌面 | pywebview(WebView2) · onedir 绿色包 · 单实例锁 · WebView2 偏好持久化 |
| 网络 | 出网策略引擎(proxy_first / direct_only / direct_first / proxy_only，直连类策略统一本机托管) · 自备 Clash 订阅 · mihomo 内核与 GeoIP 数据随包内置 |
| 认证 | 应用内账号密码登录直调 Steam IAuthenticationService；手动 Cookie 粘贴通道保留；凭据 AES-256-GCM 加密落库 |

### 业务主线

价格系统的组织主线：

```
Monitor Pool → Price Refresh Cycle → Price Observation
→ Coverage / Freshness → Price Event → 用户结果
```

- **Monitor Pool**：持续监控的对象集合，只由用户显式动作建立（关注 / 同步 / 导入）；「添加游戏」仅入 Catalog + 一次性首爬，不等于建立监控。
- **Price Refresh Cycle**：唯一的价格刷新生命周期，每轮由多段任务串成（监控池 → 库内其余 → 目录层随开关 → 特惠榜尾段恒随轮），锚点网格对齐 Steam 折扣刷新时刻。
- **Observation 与 Event 分离**：`game_price_history` 一行是一次观察；事件层（降价 / 史低 / 永降）在观察之上独立判定。
- **榜单视图**（Plus 增补）：`GET /games?board=<key>` 把榜单 appid 序 join 目录与价格行——发现面从调试端点变成用户可翻的页。

### 自动化设计

- 用户的输入边界是封闭清单（绑定账号 / 关注 / 添加订阅 / 添加游戏），此后的一切自动完成：订阅导入即体检、体检健康自动准入、出口漂移启动热收敛、重建空池自愈、数据整体过期自动提前首轮刷新。
- 停自动化走白名单：仅数据泄露 / 登录信息过期 / 订阅全失效会停下等用户，其余失败一律自动降级、重试、换通道。

## 版本方案（fork 约定）

- 版本号唯一来源 `server/app/core/app_info.py` 的 `APP_VERSION`，方案 `0.1.1-plus.N`，每次对外发布递增 N；`web/package.json` 与 README 徽章同步。
- 品牌与存储分离：`APP_NAME = HoldexarPlus`（界面/发布包/更新资产名），`APP_SLUG = holdexar`（数据目录、库文件、localStorage 键、环境变量前缀）——**改名不动数据**。
- 更新源切割：`GITHUB_REPO` 指向本仓库（应用内自更新唯一来源）；`UPSTREAM_REPO` 仅用于只读公共种子资产。

## 源码运行

要求 Python ≥3.13、Node ≥ 20.19（或 ≥ 22.12；Vite 8 的 `engines` 要求）：

```bash
git clone https://github.com/Solvora-orbit/HoldexarPlus.git
cd HoldexarPlus
python run.py            # 自动建 venv → 装依赖 → 构建前端 → 拉起桌面窗口
```

```bash
python run.py --server   # 仅本地服务，浏览器访问 http://127.0.0.1:28765
python run.py --dev      # 对接 Vite 热更新开发
python run.py --port N   # 指定端口
```

首次运行会自动补齐公共数据种子（历史汇率与价格历史切片，约 288MB）：种子是二进制大文件，不进 git，由 `run.py` 从 Release 资产拉取。没网也不影响启动，只是【汇率】页没有历史档案，之后重跑 `python scripts/fetch_seed.py` 即可。

随包的还有 Clash 内核（mihomo + GeoIP 数据，约 97MB，版本固定）：同为二进制不入库，由 `run.py` 调用 `python scripts/fetch_kernel.py` 从上游补齐；获取失败也不阻断启动，【网络】页会显示「内核缺失」并提供一键安装入口。

## 打包与发布

发布机（建议用 `server/.venv` 的解释器）：

```bash
python scripts/build_release.py          # npm build → PyInstaller → 消毒 → zip，并生成清单
#   编辑 release/RELEASE_NOTES.md 写本次 changelog（客户端检查更新直接展示这段文字）
python scripts/build_manifest.py         # 改完说明后刷新 latest.json
python scripts/publish_release.py --dry-run   # 预览发布动作，不碰网络
python scripts/publish_release.py             # 正式发布（需 gh CLI 且已 gh auth login）
```

一次正式发布会产生**两条 Release**：

| Release | tag | 内容 | 作用 |
|---|---|---|---|
| 版本发布 | `v<版本>` | zip + `latest.json` + `holdexar_seed.db` + Scoop 清单 | 给用户下载的那一条，带 changelog |
| 更新清单 | `updater` | 只有 `latest.json` | 机器读的那一条，地址恒定 |

要点：

- 清单 Release **必须不是 Latest release**（脚本已处理为 `--prerelease --latest=false`）。否则 `releases/latest` 与 RSS 会被带偏，源码用户取种子会失效。
- 资产名固定为 `HoldexarPlus-win64-v<版本>.zip`（不带时间戳），渠道自动跟进（Scoop 的 `checkver` / `autoupdate`）才能按版本号拼出 URL。
- 打包产物含 `THIRD_PARTY_NOTICES.md`（GPL-3.0 第三方组件声明，构建必需）。
- tag 已存在时脚本改走 `gh release upload --clobber`，重复发布会覆盖资产而非失败。
- 客户端检查链：清单（镜像链）→ 失败回落 GitHub API。没发过时功能照常，只是享受不到免限速与内置 SHA256。

没装 gh：`--dry-run` 仍可打印全部命令；脚本还会给出网页端手工创建的照抄清单。

## 项目结构

```
HoldexarPlus/
├── run.py            # 一键启动器
├── desktop/          # pywebview 壳 + PyInstaller spec
├── scripts/          # 发布脚本（出包 / 清单 / 发布 / 取种子 / 取内核）
├── packaging/scoop/  # Scoop 渠道用法
├── server/app/
│   ├── core/         # 配置 / 数据库 / 调度器 / 更新器 / 数据目录判定
│   ├── crawler/      # 爬核（aiohttp / 路由 / worker / 写库）
│   └── domains/      # 业务域（account/games/wishlist/family/bills/proxies/monitoring/…）
├── server/tests/     # 行为测试套件
├── web/src/          # Vue 3 + TS（components/ui = Hl* 体系，views = 页面）
└── CHANGELOG.md      # 发布变更记录
```

## 定制与反馈

欢迎 fork 后按自己的需求改造。常见定制点：

| 定制项 | 位置 |
|---|---|
| 区服列表（增删国家） | `server/app/crawler/config.py` → `CC_LIST` |
| 调度节奏（任务 / 间隔） | `server/app/core/scheduler.py` → `start_scheduler()` |
| 爬取并发与超时 | `server/app/crawler/config.py` → `DEFAULT_WORKER_COUNT` / `HTTP_TIMEOUT` |
| 主题与主题色 | `web/src/styles/tokens.css`（token 化：深浅各一套 + `data-accent` 方案覆盖块） |

有问题或功能建议，随时提 [Issue](../../issues)。参与开发：克隆后执行 `git config core.hooksPath .githooks` 启用提交门禁（敏感面 / 注释留痕 / lint / 路由 / 词典 / 旗帜素材六项）；依赖与启动见上文【源码运行】，出包与发布见【打包与发布】。

## 致谢

- [GLrone/Holdexar](https://github.com/GLrone/Holdexar) — 本项目的底座，全部核心能力源自于此
- [mihomo](https://github.com/MetaCubeX/mihomo) — Clash 代理内核（随包分发，GPL-3.0）
- [meta-rules-dat](https://github.com/MetaCubeX/meta-rules-dat) — GeoIP / GeoSite 规则数据（随包分发）
