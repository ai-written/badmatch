FROM python:3.12-slim

WORKDIR /app

ENV PYTHONPATH=/app

COPY requirements.txt .

# PyPI 源固定写死会踩坑：清华源会对部分出口网段/非浏览器特征的客户端直接返回
# 403（页面写着「您访问使用的软件带有非常用软件的特征 / 您所在的网段近期向本站
# 发送过异常请求」），此时 pip 会报 "from versions: none" —— 看起来像版本不存在，
# 实际是索引页被拒。因此改成构建参数可覆盖，默认用阿里源。
#   覆盖示例：docker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
#   官方源：  docker compose build --build-arg PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} -r requirements.txt

COPY . .

# 生产默认：单进程启动（WebSocket 广播是进程内单例，多 worker 会丢广播，
# 故不使用 --workers；开发环境由 dev compose 覆盖 command 加 --reload）
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
