/**
 * 头像上传前在浏览器端压缩。
 *
 * 背景：服务端最终只保存 100px、约 1KB 的 JPEG。若直接上传原图，
 * 用户要传 2MB 才能得到 1KB 的结果（约 2000 倍放大）。在低带宽环境下
 * 上传耗时轻易超过 axios 的 10s 超时，表现为「转圈很久后请求超时」。
 * 这里先把图缩到 200px 再编码，上传体积降到几十 KB 量级。
 *
 * 注意：浏览器渲染 <img> 时会按 EXIF 自动转正，canvas 抓到的是已转正的
 * 像素，因此输出不带方向标记也不会歪 —— 服务端的 exif_transpose 对
 * 无标记图片是空操作，方向依旧正确。
 */

// 允许选择的原图上限（压缩前）。仅在浏览器本地校验，不会上传原图。
const AVATAR_INPUT_MAX_MB = 20
// 压缩后的目标边长，略大于服务端的 100px，留出余量
const AVATAR_MAX_SIZE = 200
// 压缩后体积上限，超过则说明浏览器编码异常
const AVATAR_OUTPUT_MAX_MB = 2

const ACCEPTED_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif']

export function compressAvatar(file: File): Promise<Blob> {
  return new Promise((resolve, reject) => {
    if (file.size > AVATAR_INPUT_MAX_MB * 1024 * 1024) {
      reject(new Error(`图片不能超过 ${AVATAR_INPUT_MAX_MB}MB，请先压缩或换一张`))
      return
    }
    // 类型缺失时（部分系统给不出 MIME）交给浏览器解码能力兜底，不在这里拒绝
    if (file.type && !ACCEPTED_TYPES.includes(file.type)) {
      reject(new Error('仅支持 JPG / PNG / WebP / GIF 格式的图片'))
      return
    }

    const url = URL.createObjectURL(file)
    const img = new Image()

    const cleanup = () => URL.revokeObjectURL(url)

    img.onload = () => {
      try {
        const w = img.naturalWidth
        const h = img.naturalHeight
        if (!w || !h) throw new Error('图片尺寸异常')

        // 等比缩放；只取一侧，另一侧由浏览器按原比例算出，避免两次取整导致变形
        let dw = w
        let dh = h
        if (w >= h && w > AVATAR_MAX_SIZE) {
          dw = AVATAR_MAX_SIZE
          dh = Math.round((h * AVATAR_MAX_SIZE) / w)
        } else if (h > w && h > AVATAR_MAX_SIZE) {
          dh = AVATAR_MAX_SIZE
          dw = Math.round((w * AVATAR_MAX_SIZE) / h)
        }

        // 先画白底：服务端也会做白底合成，这里提前处理可避免 PNG 透明区域发黑
        const canvas = document.createElement('canvas')
        canvas.width = dw
        canvas.height = dh
        const ctx = canvas.getContext('2d')
        if (!ctx) throw new Error('当前浏览器不支持图片压缩')
        ctx.fillStyle = '#ffffff'
        ctx.fillRect(0, 0, dw, dh)
        ctx.drawImage(img, 0, 0, dw, dh)

        canvas.toBlob(
          (blob) => {
            cleanup()
            if (!blob) {
              reject(new Error('图片压缩失败，请换一张图片'))
              return
            }
            if (blob.size > AVATAR_OUTPUT_MAX_MB * 1024 * 1024) {
              reject(new Error('图片过大，请换一张'))
              return
            }
            resolve(blob)
          },
          'image/jpeg',
          0.85,
        )
      } catch (e) {
        cleanup()
        reject(e instanceof Error ? e : new Error('图片压缩失败'))
      }
    }

    // 浏览器无法解码（例如 iPhone 的 HEIC）时走这里
    img.onerror = () => {
      cleanup()
      reject(new Error('无法读取该图片格式，请在手机设置中改用「兼容性最佳」后重拍'))
    }

    img.src = url
  })
}
