"""持久化稀有鱼配额刷新时间，支持停机跨日后的补刷新。"""


def up(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fishing_quota_reset (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            reset_time TEXT NOT NULL
        )
    """)
