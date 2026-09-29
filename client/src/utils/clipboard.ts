/**
 * 复制文本到剪贴板。
 *
 * navigator.clipboard 只在「安全上下文」（https 或 localhost）里存在：
 * 用 http://192.168.x.x 在手机上调试时它是 undefined，直接调会抛 TypeError，
 * 表现为「点了复制没反应」。所以退回到 execCommand('copy') 的老办法——
 * 它已被标记废弃，但各端仍然可用，是目前唯一能覆盖非安全上下文的方案。
 *
 * @returns 是否复制成功（调用方据此给不同提示）
 */
export async function copyText(text: string): Promise<boolean> {
  if (!text) return false

  if (window.isSecureContext && navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // 用户拒绝授权等情况，落到下面的兜底再试一次
    }
  }

  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    // 放在视口外，避免手机键盘弹起或页面被滚动
    ta.style.cssText = 'position:fixed;top:-1000px;left:0;opacity:0'
    document.body.appendChild(ta)
    ta.select()
    // iOS 上 select() 不足以选中，需要显式给范围
    ta.setSelectionRange(0, ta.value.length)
    const ok = document.execCommand('copy')
    ta.remove()
    return ok
  } catch {
    return false
  }
}
