import sqlite3
import threading
import time
from typing import Optional
from contextlib import contextmanager

import logging

logger = logging.getLogger(__name__)
class DatabaseConnectionManager:
    """数据库连接管理器，提供线程安全的连接管理和重试机制"""
    
    def __init__(self, db_path: str, timeout: int = 30, max_retries: int = 3, retry_delay: float = 0.1):
        self.db_path = db_path
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._local = threading.local()
    
    def _get_connection(self) -> sqlite3.Connection:
        """获取一个线程安全的数据库连接"""
        # The active UoW owns lifetime; never cache a wrapper past its transaction.
        return self._create_connection()
    
    def _create_connection(self) -> sqlite3.Connection:
        """复用工作单元连接，避免旁路日期序列化及事务边界。"""
        from .unit_of_work import connection
        return connection(self.db_path)
    
    @contextmanager
    def get_connection(self):
        """获取数据库连接的上下文管理器，支持重试机制"""
        from .unit_of_work import connection
        conn = connection(self.db_path)
        try:
            yield conn
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower():
                logger.warning(f"数据库锁定，操作无法在超时 ({self.timeout}s) 内完成: {e}")
            
            # 发生严重错误时，关闭并移除线程中的无效连接
            if conn:
                try:
                    conn.close()
                except:
                    pass
            if hasattr(self._local, "connection"):
                delattr(self._local, "connection")
            raise
        except Exception as e:
            logger.error(f"数据库操作发生未知错误: {e}")
            raise
    
    def close_connection(self):
        """关闭当前线程的数据库连接"""
        if hasattr(self._local, "connection"):
            try:
                self._local.connection.close()
            except:
                pass
            finally:
                delattr(self._local, "connection")
    
    def execute_with_retry(self, query: str, params: tuple = (), fetch: str = "none"):
        """执行SQL查询，支持重试机制
        
        Args:
            query: SQL查询语句
            params: 查询参数
            fetch: 获取结果的方式 ("none", "one", "all")
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            
            if fetch == "one":
                return cursor.fetchone()
            elif fetch == "all":
                return cursor.fetchall()
            else:
                conn.commit()
                return cursor.lastrowid if cursor.lastrowid else None
