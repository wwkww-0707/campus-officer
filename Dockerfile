FROM python:3.12-slim

# 中文分词与字体
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖，利用 Docker 层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8501 8811

# 默认启动后端；界面用 docker-compose 的第二个服务拉起
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8811"]
