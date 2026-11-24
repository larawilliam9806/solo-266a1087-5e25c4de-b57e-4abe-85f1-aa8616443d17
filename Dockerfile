FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/data/exhibition.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# SQLite 数据持久化到挂载卷
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

# 单 worker：SQLite 同一时刻只允许一个写事务，
# 换交在应用层用 BEGIN IMMEDIATE 串行化即可保证并发不同日期全部生效。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
