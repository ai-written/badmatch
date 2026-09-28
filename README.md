# 爱玩羽社 - 羽毛球循环赛管理系统

移动端 H5 羽毛球 2v2 循环赛管理平台。随机轮换搭档、实时记分排名、PK 加油助威、邀请制注册。

## 技术栈

- **后端**: FastAPI + SQLAlchemy 2.0 (async) + PostgreSQL
- **前端**: Vue 3 + Vant 4 + Pinia + TypeScript
- **部署**: Docker Compose

## 快速开始

### 开发环境

```bash
git clone <repo-url> && cd BadMatch
docker compose -f docker-compose.dev.yml up --build
```

访问 `http://localhost:5173`

### 生产环境

```bash
# 1. 复制 .env.example 为 .env 并填写：
#    SECRET_KEY（必填！openssl rand -hex 32 生成）
#    DB_PASSWORD、SUPERADMIN_INIT_CODE（可选，见下）
cp .env.example .env

# 2. 启动
docker compose -f docker-compose.prod.yml up -d
```

访问 `http://localhost`（端口由 `.env` 中 `PORT` 控制，默认 80）

> 服务启动时会自动执行幂等迁移（补齐 `users.email`、唯一约束、`matches.started_at/ended_at`、`users.token_version` 等缺失列），老库升级无需手动执行 SQL。

### 首次初始化（超级管理员）

首个注册用户自动成为超级管理员，但必须提供**初始注册码**（防止被抢先注册）：

- 在 `.env` 中配置 `SUPERADMIN_INIT_CODE=你的注册码`，或
- 不配置时服务每次启动随机生成 8 位注册码并打印到日志（重启后失效）

### 忘记密码（邮箱找回）

登录页提供「忘记密码？」入口：输入邮箱 → 收到带重置链接的邮件 → 打开链接设置新密码。

- **前提一：必须配置 SMTP**（`SMTP_HOST`/`SMTP_USER`/`SMTP_PASSWORD`）。未配置时接口仍正常返回，
  但邮件发不出去、日志会提示 `密码重置邮件未发出`，用户拿不到链接。
- **前提二：该账号必须填过邮箱**。邮箱是可选字段，从未设置过邮箱的账号无法用此方式找回
  （只能由超级管理员在管理面板重置密码）。
- 安全设计：
  - 无论邮箱是否注册都返回同一句文案，避免被用来批量探测某邮箱是否注册过
  - 命中与未命中两条分支的**库操作与耗时也保持一致**（发信已移出请求路径），
    否则「响应体一样但耗时差几百毫秒」照样能被批量测出邮箱是否注册
  - 发信走后台任务 + 线程（`smtplib` 是同步阻塞调用，放在请求路径上会独占事件
    循环，实测一次阻塞 1 秒会让心跳定时器抖动 999ms，期间所有人记分/推送停摆）
  - 令牌 20 分钟有效、**只能用一次**（并发提交同一令牌也只有一次能成功），
    重新申请会作废该用户此前所有未用令牌
  - 数据库里只存令牌的 sha256，**不存明文**（库泄露也无法拿去重置别人密码）
  - 令牌放在邮件链接的 `#` 之后（URL fragment）：不会随请求发给服务器，
    因此不进 nginx/应用访问日志，也不会通过 Referer 泄露；邮件安全网关预取
    链接时也不会带上 fragment，因此不会误消费这张一次性令牌
  - 重置成功后 `token_version` 自增：所有已登录设备立即失效并断开 WebSocket，
    需要重新登录
  - 新密码上限 30 位（bcrypt 只取前 72 字节，前端输入框已按此限制）
  - 限流：申请 5 次 / 15 分钟（按来源 IP），提交 10 次 / 15 分钟（按来源 IP）
- 邮件发送使用系统 CA 校验证书（`ssl.create_default_context()`）。注意 Python 的
  `SMTP_SSL`/`starttls` 在未显式传 context 时**默认不校验证书**，而这条链路上跑的
  是可改密码的凭证，因此必须显式校验。若你的 SMTP 用的是自签证书，请改为把该证书
  加入信任链，而不是关闭校验。
- 升级说明：早期版本「修改资料」保存邮箱时只做 `strip` 不做小写化，可能存下带大写的
  邮箱。启动迁移会统一规范成小写；若历史上已存在 `A@x.com` 与 `a@x.com` 各占一个
  账号，则保留 id 较小的那个，其余账号的邮箱被置空（并在日志中告警）——被置空的账号
  将无法再用邮箱找回，只能由超级管理员在管理面板重置密码。

### 安全注意

- **WebSocket 用一次性票据连接**：浏览器无法给 WebSocket 握手加自定义头，凭证只能放进 URL，
  而 nginx 会把请求行写进 access log —— 等于把长期有效的 JWT 明文落盘。
  因此改为先用 `POST /api/auth/ws-ticket`（JWT 走 `Authorization` 头，不落日志）换一张票据，
  再用 `?ticket=xxx` 连接。票据**一次性、60 秒过期、不能换 JWT**，即使被日志记录也无利用价值。
  过渡期仍兼容旧的 `?token=<JWT>` 方式，待所有前端刷新过后可删除该兼容分支。
- **`SECRET_KEY` 校验是 fail-closed 的**：留空、使用默认/示例值、或长度不足 32 字节时，服务**拒绝启动**（不再看 `DEBUG`）。
  这是刻意的——默认密钥是公开的，一旦被用上，任何人都能离线伪造出任意用户（含超级管理员）的登录凭证。
  本地开发如确需使用默认密钥，显式设置 `ALLOW_INSECURE_SECRET_KEY=true`（dev compose 已这样配置）。
- **来源 IP 默认不信任转发头**：`CF-Connecting-IP` / `X-Forwarded-For` / `X-Real-IP` 任何客户端都能自己伪造，
  一旦采信，所有按 IP 的限流（登录、初始注册码、邀请码、头像上传）只要每次换一个伪造值就能无限尝试，
  审计日志里的来源 IP 也会变成攻击者随手填的内容。
  - 默认 `TRUST_PROXY_HEADERS=false`：直接用对端 IP（安全，但经反向代理时记录的是代理 IP）
  - 服务只允许反向代理回源时设为 `true`：此时优先取 `X-Real-IP`（nginx 用 `$remote_addr` 覆盖式写入，伪造值会被冲掉）
  - 走 Cloudflare 时需**同时**设置 `TRUST_CF_CONNECTING_IP=true`，否则限流会按 Cloudflare/nginx 的 IP 计数
- **token 版本号机制**：登出、修改密码、管理员重置密码都会使该用户所有 token 立即失效，并主动断开其全部 WebSocket 连接；升级/重启后旧 token 按版本 0 兼容处理，**已登录用户无需重新登录**

### Cloudflare 部署（推荐）

**场景 A：Cloudflare Tunnel（Zero Trust）——内网/无公网 IP 服务器**

- 服务器**无需公网 IP、无需开放任何入站端口**：`cloudflared` 主动出站连接 Cloudflare，回源走 HTTP（80 端口）
- 浏览器侧 HTTPS 和 HTTP/2 由 Cloudflare 提供，服务器**无需配置证书/443**
- 真实客户端 IP 通过 `CF-Connecting-IP` 头获取 —— 需显式设置 **`TRUST_CF_CONNECTING_IP=true`** 才会使用该头
- ⚠️ 安全：确保本地 80 端口**不对外暴露**（只允许本机 cloudflared 访问），否则可伪造 `CF-Connecting-IP` 绕过限流

**场景 B：Cloudflare 代理（橙色云，服务器有公网 IP）**

- Cloudflare 面板 **SSL/TLS 模式保持 Flexible**（回源 HTTP）；若设为 Full/Full(strict) 会回源 TLS 失败（521/525）
- ⚠️ 防火墙/安全组**只放行 Cloudflare IP 段**（https://www.cloudflare.com/ips/ ），否则直连可伪造 `CF-Connecting-IP`
- 同样需要 `TRUST_CF_CONNECTING_IP=true`

**场景 C：直接经 nginx 暴露（无 Cloudflare）**

- nginx 已用 `$remote_addr` 覆盖式写入 `X-Real-IP`，因此设置 `TRUST_PROXY_HEADERS=true` 即可拿到真实客户端 IP
- `docker-compose.prod.yml` 默认即为 `true`；**若服务会被绕过 nginx 直接访问，必须改回 `false`**

## 核心功能

- **用户名注册登录**：修改密码、上传头像、性别设置
- **邀请制注册**：生成邀请码/链接，可重新生成作废旧码
- **赛事管理**：创建、删除、提前结束，自定义计分制和场次数
- **日历选日期 + 时间段**：日期默认本周五，时间段默认 19:00~21:00，分钟步长 5
- **智能赛程**：贪心+回溯算法，保证每人等场次，搭档最大化多样
- **实时记分**：+1/-1 按钮，防抖节流，交换场地，手动结束比赛（确认弹窗），结束后自动返回
- **比赛时长统计**：记分页实时计时（localStorage 持久化，首次记分起算，超 3 小时自动重置）；结束后对阵表显示比赛耗时
- **PK 加油条**：观众可为比赛队伍投票加油，进度条实时显示票数，头像展示投票者
- **排名统计**：胜场→净胜分排序，跨赛事胜率汇总，🥇🥈🥉 奖牌展示（退赛选手不占名次）
- **积分榜分享图**：右上角一键生成 PNG 卡片（赛事名 + 名次/胜率/净胜分，当前用户高亮、退赛淡出），
  可调系统分享面板发到微信，或长按图片保存后再发
  （微信内置浏览器里「分享给微信好友」不可用：安卓微信没有 Web Share API；iOS 微信虽有、
  但把文件交给微信自己的分享扩展会被取消（面板闪一下就没了），所以微信内主推「长按图片 → 存储图像」，
  分享按钮只承诺其他应用；`http://局域网IP` 不是安全上下文，也不会有系统分享）
- **裁判认领**：先到先得，上场选手和裁判不可投票
- **管理员面板**：查看用户、删除用户、重置密码、设置/取消管理员；超级管理员可查询**操作日志**（按用户名/操作类型/时间范围筛选，分页加载）和**访问日志**（实时浏览记录，按关键词/请求方法筛选，读文件不落库）
- **访问审计**：全量 HTTP 访问日志（JSONL 落盘，含 IP/用户/路径/耗时）；关键业务操作写入 `audit_logs` 表，默认记录 90 天

### 审计覆盖的操作（25 种）

| 分类 | 操作 |
|---|---|
| 认证 | 注册、登录成功、登录失败、登出、修改密码、修改资料、上传头像、生成邀请码 |
| 管理员 | 重置密码、设置角色、删除用户 |
| 赛事 | 创建、批量创建、删除、开始、结束、退赛、报名、取消报名 |
| 比赛 | 开始轮次、结束比赛、记分*、投票*、认领裁判、释放裁判 |

> `*` 为高频操作（记分/投票），默认不记录，由 `AUDIT_HIGH_FREQ_ENABLED` 环境变量控制
>
> 高频记分/投票不写表；其余操作每次触发均写入 `audit_logs`，可在管理面板「操作日志」页查询

- **超级管理员**：可删除任意状态赛事、预选参赛人员
- **赛事自动结束**：所有比赛打完自动完结赛事并广播
- **下拉刷新**：全站页面支持下拉刷新，类 App 体验
- **WebSocket 实时同步**：计分、排名、加油条、对阵表实时更新（连接需登录 token 校验）
- **智能返回**：直接通过链接进入页面时返回首页，站内跳转则逐级返回

## 访问控制

除下面四处外，**所有页面都要求登录**（未登录访问会跳转到登录页，登录后自动回到原目标）：

| 匿名可访问 | 为什么留公开 |
|---|---|
| `/profile` | 登录/注册页，也是邀请链接的落地页（`?invite=邀请码`） |
| `/forgot-password` | 忘记密码入口，走到这里时必然还没有登录态 |
| `/reset-password` | 邮件里的重置链接，令牌在 `#` 里，打开时本来就没有登录态 |
| `/tournament/:id/rankings` | 积分榜：只读、无隐私，是这个封闭群唯一对外的展示面（分享图也在这里生成） |

**边界在后端，不在前端**：前端路由守卫只是体验层，直接 `curl` 接口绕得过去。
所以除积分榜外，赛事列表/详情/报名名单/对阵/单场/应援票数/任意用户战绩这些读取
接口都在 `server/app/api/` 里显式要求登录（`Depends(require_user)`）；匿名请求返回
401，前端拦截器会清掉 token、跳到登录页，并在登录后自动回到原目标。

> 想再放开某一页（例如让外人也能看对阵表），前后端要一起改：后端去掉对应接口的
> `require_user`，前端给该路由加 `meta: { public: true }`。只改前端等于没改。

> 手打错的地址（未匹配任何路由）会兜底跳回首页；未登录时先进登录页，登录后落到首页。

## 移动端性能优化

- **vendor 分包**：构建按 vue/vant/axios 拆分稳定 chunk，配合 `/assets/` 30 天 immutable 缓存，发版只重下业务代码
- **Service Worker**：生产环境注册，静态资源缓存优先、页面网络优先并离线兜底，二次访问几乎零网络
- **API ETag**：GET 响应带弱 ETag + `Cache-Control: private, no-cache`，内容未变时返回 304，重复访问只传协商头
- **压缩**：nginx gzip 已开启；走 Cloudflare 代理时边缘自动提供 Brotli
- **生产单进程**：镜像默认不带 `--reload`（开发环境由 dev compose 覆盖为 `--reload` 热重载）；WebSocket 广播为进程内单例，故不使用 `--workers` 多进程，避免丢广播

### 存量头像压缩

压缩只在上传时执行，历史头像需手动 backfill（压缩到 100px / JPEG 75，原地覆盖，数据库引用不变）：

```bash
docker compose -f docker-compose.prod.yml exec server python scripts/backfill_avatars.py --dry-run
docker compose -f docker-compose.prod.yml exec server python scripts/backfill_avatars.py
```

严格模式（旧 png/gif/webp 重命名为 `.jpg` 并同步 `users.avatar` 引用）：

```bash
docker compose -f docker-compose.prod.yml exec server python scripts/backfill_avatars.py --rename-jpg --dry-run
docker compose -f docker-compose.prod.yml exec server python scripts/backfill_avatars.py --rename-jpg --apply-db
```

> 头像路径只存在 `users.avatar` 一列；`audit_logs.detail` 中的历史路径快照不会更新（审计留痕）。

> 注意：原地覆盖模式因为 `/static` 有 7 天浏览器缓存，用户可能最长 7 天看到旧头像；
> 严格模式重命名后 URL 变化，头像立即刷新。

> 严格模式会把**所有**非 jpg 头像（无论大小）转成 JPEG 并改后缀，包括之前原地压缩过的文件；
> 若之前已确认 Cloudflare 缓存，执行后可顺手在 CF 后台按 `/static/uploads/` 前缀 Purge 一次。

## 项目结构

```
├── server/               # FastAPI 后端
│   ├── app/
│   │   ├── api/          # auth / tournaments / matches / rankings / engine / referee / notifications
│   │   ├── core/         # 配置、数据库、安全、WebSocket、幂等启动迁移
│   │   ├── engine/       # 赛程引擎
│   │   ├── models/       # 数据模型
│   │   └── schemas/      # Pydantic 模型
│   ├── tests/            # pytest 单元测试
│   ├── alembic/          # 未启用的 Alembic 配置（无 versions/，见下方说明）
│   └── static/uploads/   # 用户头像
├── client/               # Vue 3 前端
│   ├── src/views/        # 页面组件
│   ├── src/stores/       # Pinia 状态
│   ├── src/api/          # Axios 封装
│   ├── src/composables/  # WebSocket / 返回逻辑 等
│   └── public/           # 静态资源
├── docker-compose.yml        # 生产环境
├── docker-compose.dev.yml    # 开发环境（Vite HMR）
├── docker-compose.prod.yml   # Docker Hub 部署
├── publish.sh                # 镜像发布脚本
└── .env.example              # 环境变量模板
```

### 数据库结构调整（重要）

本项目**不使用 Alembic 管理 schema**，而是：

1. `Base.metadata.create_all` —— 只负责创建新表，不会给已存在的表补列
2. `app/core/startup_migration.py` —— 启动时**幂等**检查并补齐老库缺失的列/约束（新库老库都能直接跑起来）

因此**新增字段时**：改 `models/` 里的模型，并在 `startup_migration.py` 里加一段「列不存在才 ALTER TABLE」的逻辑，
部署后重启服务即自动生效，**无需手工执行 SQL**。

`server/alembic/` 只保留了当初为 Alembic 准备的异步 `env.py` 与 `alembic.ini`，**没有 `versions/` 目录、代码也未引用它**。
如果将来要改用 Alembic，需要自己生成初始迁移并停止使用 `startup_migration.py`，避免两套机制并行。

## 计分规则

- N 分制（创建赛事时设定，默认 11）
- 无自动结束，裁判手动点击"结束比赛"
- 双打循环赛，胜率按参与场次统计

## 测试

```bash
cd server
pip install -r requirements.txt
pytest tests
```
