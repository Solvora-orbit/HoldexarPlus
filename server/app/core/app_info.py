"""应用元信息：唯一的品牌常量来源（改名只改这里）。

APP_NAME  → 界面标题 / FastAPI title / 窗口标题
APP_SLUG  → 数据库文件名 / 日志文件名 / 数据目录名 / 环境变量前缀
GITHUB_REPO → 发布仓库（应用自更新唯一来源，fork 后只改这里）
UPSTREAM_REPO → 上游原仓库（只读自举资产，如汇率种子；非更新链路）
MANIFEST_*  → 更新清单的固定落点（客户端检查更新与发布脚本共用同一地址）
"""

APP_NAME = "HoldexarPlus"
APP_SUBTITLE = "Steam 多区价格监控终端"
# slug 保持 "holdexar"：数据目录（%LOCALAPPDATA%\holdexar[-dev]）、库文件名、
# 环境变量前缀 HOLDEXAR_ 全部沿用——显示名升级为 Plus，本地数据不迁移不丢失
APP_SLUG = "holdexar"

# 版本号：**全项目唯一来源**（config.Settings.version 只是把它接进 pydantic 设置，
# 便于环境变量覆盖；发布脚本读的也是这里）。发版时只改这一行。
# 放在 app_info 而非 config：config 要 import pydantic，而发布/构建脚本、
# run.py、desktop/main.py 需要在装依赖之前就能取到版本号。
# fork 版本方案（0.2.0 起转正式）：0.1.1-plus.N 开发线已结束（plus.3 为末版）；
# 正式版按 semver 走——新增功能升次版本（0.2.0）、修 bug 升修订号（0.2.1…）。
# 0.2.0 > 0.1.1-plus.3 严格成立，老客户端可正常收到升级。
APP_VERSION = "0.2.1"

# 发布仓库（owner/repo）：应用内自更新清单、发布脚本（release/publish/build_manifest）
# 与 /info 展示的唯一来源。HoldexarPlus fork 基线：只认本仓库的发布——原作者发新版
# 不会再影响本应用（fork 尚无 Release 时，更新检查按「资产不存在」优雅报无更新）。
GITHUB_REPO = "Solvora-orbit/HoldexarPlus"

# 上游原仓库：**只**用于只读的公开静态资产（fetch_seed.py 的汇率档案种子等自举
# 数据），不是更新链路——上游发新版不会进入本应用。等本仓库开始随发布自行导出
# 种子资产后，可把 fetch_seed.py 切回 GITHUB_REPO 并删除此常量。
UPSTREAM_REPO = "GLrone/Holdexar"

# 更新清单落在一个**固定 tag** 的 Release 资产下，与版本号解耦：
# 客户端检查更新永远只读这一个地址，不打 api.github.com（免限速、国内可达）。
# 发布端的对应动作在 scripts/publish_release.py（刷该 tag 的资产）。
MANIFEST_TAG = "updater"
MANIFEST_ASSET = "latest.json"
MANIFEST_SCHEMA = 1

# 换装暂存落点与交接标记：desktop/main.py 与 app/core/updater.py 共用一份——
# 写标记的一方与判标记的一方各写一套字符串，必然漂移成「落下去了但没判住」。
# manifest = 暂存就绪待换装；unsupported = 该暂存包不支持安全换装（二者在暂存目录内）；
# swap_failed = 上次换装失败并已回滚，同一安装不再自动换装——它描述的是**安装状态**
# 而不是某个包，所以落在**数据目录**：换装进程跑在暂存目录里删不掉自己，
# 而作废清理要等下一次启动由现装进程执行，标记不能被那次清理带走。
STAGING_DIR_NAME = "update-staging"
STAGING_MANIFEST_NAME = "manifest.json"
HANDOFF_UNSUPPORTED_MARK = ".handoff-unsupported"
SWAP_FAILED_FLAG = ".swap-failed"

# 环境变量前缀（pydantic-settings 用；改品牌时如需改前缀在此同步）
ENV_PREFIX = "HOLDEXAR_"
