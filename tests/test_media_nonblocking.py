"""阻塞文件/Pillow操作不能占用宿主事件循环线程。"""
from io import BytesIO
from pathlib import Path

import asyncio
import threading

import pytest
from PIL import Image

from fishing_port import media
from fishing_port.events import Reply
from test_native_adapter import command, load_plugin


@pytest.mark.asyncio
async def test_image_decode_cache_and_read_leave_event_loop(tmp_path, monkeypatch):
    loop_thread = threading.get_ident()
    opened_on = []
    original_open = Image.open

    def tracked_open(*args, **kwargs):
        opened_on.append(threading.get_ident())
        return original_open(*args, **kwargs)

    stream = BytesIO()
    Image.new('RGB', (32, 32), 'red').save(stream, format='PNG')
    downloads = []

    async def download(url):
        downloads.append(url)
        return stream.getvalue()

    monkeypatch.setattr(media, 'download', download)
    monkeypatch.setattr(Image, 'open', tracked_open)
    for _ in range(2):
        image = await media.cached_image('https://example.com/fish.png', tmp_path, 'cache', 60, 16)
        assert image.size == (16, 16)
        assert image.getpixel((0, 0)) == (255, 0, 0, 255)
    assert len(downloads) == 1
    assert opened_on and all(t != loop_thread for t in opened_on)


@pytest.mark.asyncio
async def test_help_render_and_image_read_leave_event_loop(tmp_path, monkeypatch):
    from fishing_port.handlers import common_handlers

    p = await load_plugin(tmp_path)
    loop_thread = threading.get_ident()
    rendered_on, read_on = [], []
    original_read = Path.read_bytes

    def render():
        rendered_on.append(threading.get_ident())
        return Image.new('RGB', (32, 32), 'blue')

    def read(path):
        read_on.append(threading.get_ident())
        return original_read(path)

    monkeypatch.setattr(common_handlers, 'draw_help_image', render)
    monkeypatch.setattr(Path, 'read_bytes', read)
    try:
        await command(p, '钓鱼帮助')
        assert p.ctx.send.messages[-1][0] == 'image'
        assert rendered_on and all(t != loop_thread for t in rendered_on)
        assert read_on and all(t != loop_thread for t in read_on)
        # 文件不存在时，仍发送原有文字降级内容。
        await p.send_reply(Reply('image', str(tmp_path / 'missing.png'), '帮助文字'), 's')
        assert p.ctx.send.messages[-1] == ('text', '帮助文字', 's')
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_cancelled_render_keeps_command_lock_until_worker_finishes(tmp_path, monkeypatch):
    from fishing_port.handlers import common_handlers

    p = await load_plugin(tmp_path)
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()

    def render():
        loop.call_soon_threadsafe(started.set)
        if not release.wait(5):
            raise RuntimeError('绘图线程等待超时')
        return Image.new('RGB', (32, 32))

    monkeypatch.setattr(common_handlers, 'draw_help_image', render)
    task = asyncio.create_task(command(p, '钓鱼帮助'))
    try:
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()  # 卸载/超时的重复取消不能提前放开固定输出文件。
        await asyncio.sleep(0)
        assert not task.done()
        assert p.lock.locked()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not p.lock.locked()
        await command(p, '注册')
        assert '注册成功' in p.ctx.send.messages[-1][1]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await p.on_unload()


@pytest.mark.asyncio
async def test_cancelled_shutdown_releases_process_lock_after_worker_finishes(tmp_path, monkeypatch):
    p = await load_plugin(tmp_path)
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    pool = p.engine.game_mechanics_service.thread_pool
    shutdown = pool.shutdown

    def delayed_shutdown(**kwargs):
        loop.call_soon_threadsafe(started.set)
        if not release.wait(5):
            raise RuntimeError('关闭线程池等待超时')
        shutdown(**kwargs)

    monkeypatch.setattr(pool, 'shutdown', delayed_shutdown)
    task = asyncio.create_task(p.on_unload())
    try:
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        assert p.process_lock is not None
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert p.process_lock is None
        assert p.engine is None
        replacement = await load_plugin(tmp_path)
        await replacement.on_unload()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await p.on_unload()
