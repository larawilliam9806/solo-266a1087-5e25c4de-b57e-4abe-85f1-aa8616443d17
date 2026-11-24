"""应用配置。"""
import os
from pathlib import Path

# SQLite 数据库文件路径，可通过环境变量覆盖（docker-compose 挂载卷时使用）。
DB_PATH = Path(os.getenv("DB_PATH", "/data/exhibition.db"))

# 等待写锁的最长时间（秒）。并发换交不同日期时，第二个事务会短暂等待。
BUSY_TIMEOUT_SECONDS = 30
