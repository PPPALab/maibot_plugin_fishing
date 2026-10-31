"""Bounded public-network image cache, no host absolute path dependencies."""
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

import asyncio
import hashlib
import ipaddress
import logging
import os
import socket
import time
import uuid

from PIL import Image, ImageDraw

import aiohttp

from .blocking import run_blocking

logger = logging.getLogger(__name__)
MAX_IMAGE = 5 * 1024 * 1024
MAX_CACHE = 100 * 1024 * 1024


class PublicResolver(aiohttp.abc.AbstractResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        answers = await asyncio.get_running_loop().getaddrinfo(host, port, family=family, type=socket.SOCK_STREAM)
        records = []
        for fam, _, proto, _, address in answers:
            if not ipaddress.ip_address(address[0]).is_global:
                raise ValueError('禁止访问非公网图片地址')
            records.append(dict(hostname=host, host=address[0], port=port, family=fam, proto=proto, flags=socket.AI_NUMERICHOST))
        return records

    async def close(self):
        pass


def placeholder(size):
    image = Image.new('RGBA', (size, size), '#e6eff7')
    draw = ImageDraw.Draw(image)
    draw.ellipse((size*.1, size*.3, size*.7, size*.7), fill='#779bb7')
    draw.polygon([(size*.65, size*.5), (size*.9, size*.25), (size*.9, size*.75)], fill='#779bb7')
    draw.ellipse((size*.23, size*.42, size*.3, size*.49), fill='white')
    return image


async def download(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('非法图片 URL')
    try:
        literal = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        literal = None
    if literal and not literal.is_global:
        raise ValueError('禁止访问本地/私网地址')
    connector = aiohttp.TCPConnector(resolver=PublicResolver())
    timeout = aiohttp.ClientTimeout(total=8, connect=3)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        # No redirects: cannot silently enter metadata/private endpoints.
        async with session.get(url, allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError(f'图片 HTTP {response.status}')
            chunks, size = [], 0
            async for chunk in response.content.iter_chunked(65536):
                size += len(chunk)
                if size > MAX_IMAGE:
                    raise ValueError('图片超过5MiB')
                chunks.append(chunk)
            return b''.join(chunks)


def _read_cached_image(path, ttl, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and time.time() - path.stat().st_mtime < ttl:
        with Image.open(path) as source:
            image = source.convert('RGBA')
        image.thumbnail((size, size), Image.Resampling.LANCZOS)
        return image


def _store_image(content, path, size):
    with Image.open(BytesIO(content)) as source:
        if source.width * source.height > 20_000_000:
            raise ValueError('图片像素过大')
        image = source.convert('RGBA')
    image.thumbnail((640, 640), Image.Resampling.LANCZOS)
    temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    try:
        image.save(temporary, format='PNG')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    image.thumbnail((size, size), Image.Resampling.LANCZOS)
    return image


async def cached_image(url, data_dir, category, ttl, size, fallback=None):
    path = Path(data_dir) / category / (hashlib.sha256(url.encode()).hexdigest() + '.png')
    try:
        image = await run_blocking(_read_cached_image, path, ttl, size)
        if image is None:
            content = await download(url)
            image = await run_blocking(_store_image, content, path, size)
        return image
    except Exception as error:
        logger.debug('图片不可用，使用占位图: %s', error)
        return fallback.copy() if fallback is not None else placeholder(size)


async def get_fish_icon(icon_url, data_dir, icon_size=60, *, fish_name='鱼', rarity=1):
    from .draw.fish_art import fish_illustration
    illustration = await run_blocking(fish_illustration, fish_name, icon_size, rarity)
    if not icon_url or not icon_url.strip():
        return illustration
    return await cached_image(icon_url.strip(), data_dir, 'fish_icon_cache', 604800, icon_size,
                              fallback=illustration)


async def get_user_avatar(user_id, data_dir, avatar_size=50):
    from .draw.utils import avatar_postprocess
    if not str(user_id).isdecimal():
        return placeholder(avatar_size)
    image = await cached_image(f'https://q4.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=640',
                               data_dir, 'avatar_cache', 86400, avatar_size)
    return await run_blocking(avatar_postprocess, image, avatar_size)


def cleanup_cache(data_dir, now=None):
    now = time.time() if now is None else now
    files = []
    for category, ttl in [('fish_icon_cache', 604800), ('avatar_cache', 86400), ('tmp', 86400)]:
        directory = Path(data_dir) / category
        if not directory.exists():
            continue
        for path in directory.iterdir():
            if not path.is_file() or path.is_symlink():
                continue
            stat = path.stat()
            if now - stat.st_mtime > ttl:
                path.unlink(missing_ok=True)
            else:
                files.append((stat.st_mtime, stat.st_size, path))
    total = sum(size for _, size, _ in files)
    for _, size, path in sorted(files):
        if total <= MAX_CACHE:
            break
        path.unlink(missing_ok=True)
        total -= size
