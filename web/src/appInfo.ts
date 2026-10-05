/** 应用元信息：唯一的品牌常量来源（改名只改这里）。 */
export const APP_NAME = 'HoldexarPlus'
// slug 保持 'holdexar'：localStorage 键（holdexar.theme / holdexar.locale / …）
// 全部沿用——显示名升级为 Plus，用户偏好不因改名丢失
export const APP_SLUG = 'holdexar'

/** 主题（当前固定深色；支持浅色后由主题状态决定） */
export const THEME = 'dark' as 'dark' | 'light'

/** 主题对应 logo（素材：用户提供的双版本 ico） */
export const APP_LOGO = THEME === 'dark' ? '/assets/logo_dark.ico' : '/assets/logo_light.ico'
