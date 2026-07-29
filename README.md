# BadMatch - 羽毛球循环赛管理系统

移动端 H5 羽毛球 2v2 循环赛管理平台。随机轮换搭档、实时记分排名、邀请制注册。

## 技术栈

- **后端**: FastAPI + SQLAlchemy 2.0 (async) + PostgreSQL + Redis
- **前端**: Vue 3 + Vant 4 + Pinia + TypeScript
- **部署**: Docker Compose

## 快速开始

### 开发环境

```bash
git clone <repo-url> && cd BadMatch
docker compose up --build
```

访问 `http://localhost:3020`

### 生产环境

只需两个文件即可部署，无需拉取完整代码：

```bash
# 下载部署文件
curl -O https://raw.githubusercontent.com/.../docker-compose.prod.yml
curl -O https://raw.githubusercontent.com/.../.env.example
cp .env.example .env   # 编辑修改密码和密钥

docker compose -f docker-compose.prod.yml up -d
```

或直接使用 Docker Hub 镜像：

```bash
docker run -d --name badmatch-db -e POSTGRES_USER=badminton -e POSTGRES_PASSWORD=xxx -e TZ=Asia/Shanghai postgres:16-alpine
docker run -d --name badmatch-server -e DATABASE_URL=... -e SECRET_KEY=... hsiangleev/badmatch:server-latest
docker run -d -p 80:80 hsiangleev/badmatch:client-latest
```

## 核心功能

- **用户名注册登录**：支持修改密码、上传头像、性别设置
- **邀请制注册**：已注册用户可生成邀请码/邀请链接，可重新生成作废旧码
- **赛事管理**：创建、删除、提前结束，自定义计分制和场次数
- **智能赛程**：贪心+回溯算法，保证每人等场次，搭档最大化多样
- **实时记分**：N 分制（默认 11）+ 领先 2 分获胜，WebSocket 实时同步
- **排名统计**：胜场→净胜分排序，跨赛事胜率汇总，点击头像查看他人战绩
- **裁判认领**：先到先得，上场选手不可认领本场比赛
- **管理员面板**：查看/删除用户，重置密码
- **提前结束**：支持手动结束比赛和赛事

## 发布镜像

```bash
./publish.sh        # latest
./publish.sh v1.0   # 指定版本
```

镜像发布到 [hsiangleev/badmatch](https://hub.docker.com/r/hsiangleev/badmatch)

## 项目结构

```
├── server/               # FastAPI 后端
│   ├── app/
│   │   ├── api/          # auth / tournaments / matches / rankings / engine
│   │   ├── core/         # 配置、数据库、安全、WebSocket
│   │   ├── engine/       # 赛程引擎
│   │   ├── models/       # 数据模型
│   │   └── schemas/      # Pydantic 模型
│   └── static/uploads/   # 用户头像（不纳入版本控制）
├── client/               # Vue 3 前端
│   ├── src/views/        # 页面组件
│   ├── src/stores/       # Pinia 状态
│   └── nginx.conf        # Nginx 配置
├── docker-compose.yml        # 开发环境
├── docker-compose.prod.yml   # 生产环境
├── publish.sh                # 镜像发布脚本
└── .env.example              # 环境变量模板
```

## 计分规则

- N 分制（创建赛事时设定，默认 11）
- 必须领先 2 分才能结束
- 无封顶分，裁判可手动提前结束比赛
- 双打循环赛，胜率按参与场次统计
