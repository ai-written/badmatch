/**
 * 分享遮罩（ShareImageOverlay.vue）的环境判定与文案规则。
 *
 * 单独抽出来是为了能单测：iOS 分支（`<a download>` 只落「文件」App、系统面板里选微信
 * 变成**文件气泡**）在开发机上没法真机验证，一张分支表能防止以后"顺手简化"时又把人
 * 引回必然出错的路径。组件只负责渲染，规则都在这。
 *
 * 背景（实测踩到的，**只发生在 iOS**）：iOS 微信的分享扩展只把**相册里的照片**当图片收；
 * 网页经 `navigator.share({files})` 传过去的文件，它一律按"文件"处理——对方收到一条
 * `报名海报.png` 的文件气泡，还得自己再保存一次。网页侧改 mime / 文件名都改不掉。
 * 能进相册的只有系统动作：系统分享面板里的「存储图像」、或长按图片存到相册
 * （微信自家那套长按菜单里这一项叫法各版本不同，所以提示语只写「存到相册」，
 * 只有 iOS 系统面板那个动作是可以确定叫「存储图像」的）。
 *
 * 安卓微信没有这个毛病：系统分享是把 `image/png` 作为 `ACTION_SEND` 交给微信的，
 * 收到的是图片（用户实测正常），所以安卓/桌面完全不动，只有 iOS 需要换路径。
 */
export interface ShareEnv {
  /** iPhone / iPad（含 iPadOS 13+ 桌面 UA 的伪装） */
  isIOS: boolean
  /** 微信内置浏览器 */
  isWeChat: boolean
  /** navigator.canShare({files}) 通过（拿真实 File 试探过） */
  canShareFile: boolean
  /** window.isSecureContext：http://局域网IP 下浏览器不提供 Web Share API */
  isSecureContext: boolean
}

export function detectIsIOS(ua: string, platform: string, maxTouchPoints: number): boolean {
  // iPadOS 13+ 的 Safari UA 与 macOS 完全一样，只能靠"触摸点数"认出来
  return /iPad|iPhone|iPod/.test(ua) || (platform === 'MacIntel' && maxTouchPoints > 1)
}

export function isWeChatUA(ua: string): boolean {
  return /MicroMessenger/i.test(ua)
}

/** 遮罩下方那行提示语（调用方给了 tip 就用调用方的，不走这里） */
export function shareTip(env: ShareEnv): string {
  if (env.isIOS) {
    if (env.isWeChat) {
      // 微信自己那套长按菜单各版本叫法不一（「存储图像」/「保存图片」），别写死动作名
      return '微信内不能一键分享给好友：长按图片存到相册，再回聊天窗口以图片发送'
    }
    if (env.canShareFile) {
      return '苹果手机上选「微信」发出去的是「文件」不是图片（对方还得再存一次）：请点下方按钮，在系统面板里选「存储图像」，或长按图片存到相册，再回微信以图片发送'
    }
    // 没有系统分享的原因不止「不是 https」：有的 App 内置浏览器（钉钉/飞书/QQ）
    // 就是不给 navigator.canShare，这时说「http 访问」是错的
    return env.isSecureContext
      ? '当前浏览器不提供系统分享面板：请长按图片存到相册，再回微信以图片发送'
      : 'http 访问下浏览器不提供系统分享面板：请长按图片存到相册，再回微信以图片发送'
  }
  // 先判微信：iOS 微信里 canShare 是 true，但选「微信」必然失败
  if (env.isWeChat) {
    return env.canShareFile
      ? '微信内不能一键分享给好友：长按图片 → 存储图像，再回聊天窗口发送；发给其他应用可用下方按钮'
      : '微信内不能一键分享：长按图片 → 存储图像，再回聊天窗口发送'
  }
  if (env.canShareFile) return '点下方按钮可直接选微信发送；也可以长按图片保存后再发'
  // 按钮消失得给个理由，否则用户只会觉得"功能坏了"
  if (!env.isSecureContext) return 'http 访问下浏览器不开放系统分享（线上 https 才有分享按钮）：长按图片保存后再发送'
  return '长按图片保存到相册，或点「保存图片」'
}

export type ShareAction = 'iosSave' | 'save' | 'share' | 'savePlain'

/**
 * 遮罩里要渲染哪些按钮（数组顺序 = 渲染顺序，`close` 恒定存在不入表）。
 *
 * 抽出来是因为「iOS 只给一条路、非 iOS 保持原样」这个契约只写在模板的 v-if 里，
 * 少写一个 `!isIOS &&` 全套测试都不会红。这张表有单测（见 tests/unit/shareEnv.test.mjs）。
 */
export function visibleActions(env: ShareEnv): ShareAction[] {
  // iOS：另外两个入口都到不了相册，只留「存到相册 / 分享」
  if (env.isIOS) return ['iosSave']
  const actions: ShareAction[] = []
  if (env.isWeChat || !env.canShareFile) actions.push('save')
  if (env.canShareFile) actions.push('share')
  if (env.canShareFile && !env.isWeChat) actions.push('savePlain')
  return actions
}

/** iOS 上唯一的主按钮（别无选择：另外两个入口只会得到文件） */
export function saveButtonLabel(env: ShareEnv): string {
  return env.canShareFile ? '存到相册 / 分享' : '怎么存到相册？'
}

/** 非 iOS 的系统分享按钮文案：微信内只承诺"其他应用"（发给微信自己会失败） */
export function shareButtonLabel(env: ShareEnv): string {
  return env.isWeChat ? '分享到其他应用（QQ/邮件等）' : '分享到微信 / 其他应用'
}
