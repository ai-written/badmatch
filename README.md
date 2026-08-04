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
docker compose up -d
```

访问 `http://localhost:3020`

## 核心功能

- **用户名注册登录**：修改密码、上传头像、性别设置
- **邀请制注册**：生成邀请码/链接，可重新生成作废旧码
- **赛事管理**：创建、删除、提前结束，自定义计分制和场次数
- **日历选日期 + 时间段**：日期默认本周五，时间段默认 19:00~21:00，分钟步长 5
- **智能赛程**：贪心+回溯算法，保证每人等场次，搭档最大化多样
- **实时记分**：+1/-1 按钮，防抖节流，交换场地，手动结束比赛（确认弹窗）
- **PK 加油条**：观众可为比赛队伍投票加油，进度条实时显示票数，头像展示投票者
- **排名统计**：胜场→净胜分排序，跨赛事胜率汇总，🥇🥈🥉 奖牌展示
- **裁判认领**：先到先得，上场选手和裁判不可投票
- **管理员面板**：查看/删除用户，重置密码
- **下拉刷新**：全站页面支持下拉刷新，类 App 体验
- **WebSocket 实时同步**：计分、排名、加油条、对阵表实时更新

## 项目结构

```
├── server/               # FastAPI 后端
│   ├── app/
│   │   ├── api/          # auth / tournaments / matches / rankings / engine
│   │   ├── core/         # 配置、数据库、安全、WebSocket
│   │   ├── engine/       # 赛程引擎
│   │   ├── models/       # 数据模型
│   │   └── schemas/      # Pydantic 模型
│   └── static/uploads/   # 用户头像
├── client/               # Vue 3 前端
│   ├── src/views/        # 页面组件
│   ├── src/stores/       # Pinia 状态
│   ├── src/api/          # Axios 封装
│   ├── src/composables/  # WebSocket 等
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
