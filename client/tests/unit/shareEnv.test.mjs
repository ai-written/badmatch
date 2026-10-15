/**
 * 分享遮罩的环境判定 / 按钮表 / 文案分支：`cd client && npm run test:unit`（node --test）。
 *
 * 为什么单独测这些字符串和数组：iOS 上「保存图片」和「分享到微信」两个入口都到不了相册
 * （前者落「文件」App，后者在聊天里变成**文件气泡**，对方还得再存一次），而开发机上
 * 没有 iPhone 可验。这张分支表把"哪个环境给哪条路"钉住，改动文案时至少不会把 iOS
 * 又引回必然出错的路径，也不会顺手弄坏安卓/桌面的按钮组合。
 *
 * 依赖 Node 直接执行 .ts 的类型擦除（Node ≥ 22.18 / 23.6+ 默认开启）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  detectIsIOS, isWeChatUA, shareTip, saveButtonLabel, shareButtonLabel, visibleActions,
} from '../../src/utils/shareEnv.ts'

const ANDROID_UA = 'Mozilla/5.0 (Linux; Android 13; PGT-AN10) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36'
const IPHONE_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Version/17.5 Mobile/15E148 Safari/604.1'
const MAC_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.5 Safari/605.1.15'
const WECHAT_IPHONE_UA = `${IPHONE_UA} MicroMessenger/8.0.49(0x18003123) NetType/WIFI Language/zh_CN`

const env = (over = {}) => ({
  isIOS: false, isWeChat: false, canShareFile: true, isSecureContext: true, ...over,
})

test('detectIsIOS：iPhone UA 与 iPadOS 桌面伪装都算 iOS，Mac/安卓不算', () => {
  assert.equal(detectIsIOS(IPHONE_UA, 'iPhone', 5), true)
  // iPadOS 13+ 的 UA 与 macOS 一样，只能靠触摸点数区分
  assert.equal(detectIsIOS(MAC_UA, 'MacIntel', 5), true)
  assert.equal(detectIsIOS(MAC_UA, 'MacIntel', 0), false)
  assert.equal(detectIsIOS(ANDROID_UA, 'Linux armv8l', 5), false)
})

test('isWeChatUA：只认微信内置浏览器', () => {
  assert.equal(isWeChatUA(WECHAT_IPHONE_UA), true)
  assert.equal(isWeChatUA(IPHONE_UA), false)
})

test('visibleActions：iOS 只给一个入口（另外两个到不了相册）', () => {
  for (const over of [{}, { canShareFile: false }, { isWeChat: true }, { isSecureContext: false }]) {
    assert.deepEqual(visibleActions(env({ isIOS: true, ...over })), ['iosSave'])
  }
})

test('visibleActions：安卓/桌面的按钮组合与改动前一致', () => {
  // 安卓 Chrome / 桌面（能分享文件）：主分享 + 次保存
  assert.deepEqual(visibleActions(env()), ['share', 'savePlain'])
  // 微信内能分享文件时（就是 iOS 微信，安卓 X5 没有 Web Share）：保存为主 + 分享降级
  assert.deepEqual(visibleActions(env({ isWeChat: true })), ['save', 'share'])
  // 微信内 / http 局域网 不能分享文件：只剩保存
  assert.deepEqual(visibleActions(env({ isWeChat: true, canShareFile: false })), ['save'])
  assert.deepEqual(visibleActions(env({ canShareFile: false })), ['save'])
})

test('shareTip(iOS, 非微信)：说清选微信=发文件，并指到「存储图像」', () => {
  const t = shareTip(env({ isIOS: true }))
  assert.match(t, /文件/)
  assert.match(t, /存储图像/)
  assert.match(t, /对方还得再存一次/)
})

test('shareTip(iOS, 没有系统分享)：按真实原因解释，别一律甩给 http', () => {
  const https = shareTip(env({ isIOS: true, canShareFile: false, isSecureContext: true }))
  assert.match(https, /当前浏览器不提供系统分享面板/)
  assert.doesNotMatch(https, /http 访问/)

  const http = shareTip(env({ isIOS: true, canShareFile: false, isSecureContext: false }))
  assert.match(http, /http 访问下浏览器不提供系统分享面板/)
})

test('shareTip(iOS 微信内)：只教长按存相册，不提「分享到微信」也不提 http', () => {
  const t = shareTip(env({ isIOS: true, isWeChat: true }))
  // 这条必须是 iOS 分支的文案：安卓微信那条写的是「长按图片 → 存储图像」
  assert.match(t, /长按图片存到相册/)
  assert.match(t, /聊天窗口/)
  assert.doesNotMatch(t, /发给其他应用可用下方按钮/)
})

test('shareTip(安卓/桌面, 微信内)：主推长按，分享只承诺其他应用', () => {
  const t = shareTip(env({ isWeChat: true }))
  assert.match(t, /长按/)
  assert.match(t, /其他应用/)
  assert.doesNotMatch(t, /存到相册/)
})

test('shareTip(安卓/桌面, 非微信, 可分享)：才允许说「可直接选微信发送」', () => {
  assert.match(shareTip(env()), /可直接选微信发送/)
  assert.match(shareTip(env({ isSecureContext: false, canShareFile: false })), /http 访问/)
  assert.equal(shareTip(env({ canShareFile: false })), '长按图片保存到相册，或点「保存图片」')
})

test('saveButtonLabel：iOS 主按钮要么调系统面板，要么给长按说明', () => {
  assert.equal(saveButtonLabel(env({ isIOS: true })), '存到相册 / 分享')
  assert.equal(saveButtonLabel(env({ isIOS: true, canShareFile: false })), '怎么存到相册？')
})

test('shareButtonLabel：微信内不承诺「分享到微信」', () => {
  assert.equal(shareButtonLabel(env()), '分享到微信 / 其他应用')
  assert.equal(shareButtonLabel(env({ isWeChat: true })), '分享到其他应用（QQ/邮件等）')
})
