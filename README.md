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

### 安全注意

- 生产模式（`DEBUG=false`）下若使用默认/示例 `SECRET_KEY`，服务将**拒绝启动**
- **token 版本号机制**：登出、修改密码、管理员重置密码都会使该用户所有 token 立即失效，并主动断开其全部 WebSocket 连接；升级/重启后旧 token 按版本 0 兼容处理，**已登录用户无需重新登录**

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
