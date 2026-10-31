"""将不访问数据库的阻塞工作移出事件循环，并保留调用方的资源生命周期。"""
from functools import partial
from typing import Callable, TypeVar
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, copy_context

import asyncio

T = TypeVar('T')
_in_worker = ContextVar('fishing_worker', default=False)


async def run_blocking(function: Callable[..., T], *args, **kwargs) -> T:
    """取消时等待线程结束，防止调用方提前释放绘图文件锁或关闭资源。"""
    if _in_worker.get():
        # 已在完整操作的工作线程中；不能把事务连接再传到绘图线程。
        return function(*args, **kwargs)
    work = asyncio.create_task(asyncio.to_thread(partial(function, *args, **kwargs)))
    return await drain_on_cancel(work)


async def drain_on_cancel(work):
    """资源所有者必须等在途工作结束，重复取消也不能提前释放锁。"""
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        # to_thread 无法中断正在运行的线程；重复取消也不能跳过资源收尾。
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not work.cancelled():
            work.exception()  # 取走线程异常；对调用方保留原始取消语义。
        raise


class GameWorker:
    """单一串行边界；协程及其 SQLite 连接在同一工作线程完整执行。"""

    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='fishing-db')

    async def run(self, operation, *args):
        context = copy_context()

        def execute():
            async def invoke():
                token = _in_worker.set(True)
                try:
                    return await operation(*args)
                finally:
                    _in_worker.reset(token)
            return context.run(asyncio.run, invoke())

        work = asyncio.get_running_loop().run_in_executor(self.executor, execute)
        return await drain_on_cancel(work)

    async def close(self):
        await run_blocking(self.executor.shutdown, wait=True, cancel_futures=True)
