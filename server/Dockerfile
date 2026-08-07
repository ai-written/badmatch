FROM python:3.12-slim

WORKDIR /app

ENV PYTHONPATH=/app

COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

COPY . .

# 生产默认：单进程启动（WebSocket 广播是进程内单例，多 worker 会丢广播，
# 故不使用 --workers；开发环境由 dev compose 覆盖 command 加 --reload）
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
