"""Regression tests run with DeprecationWarning treated as an error."""
from datetime import date, datetime, timedelta, timezone
import sqlite3

from PIL import Image
import pytest

from fishing_port.core.database.unit_of_work import atomic
from fishing_port.core.domain.models import UserBuff
from fishing_port.draw.help import replace_white_background
from test_native_adapter import command, load_plugin


def test_explicit_sqlite_dates_and_bulk_parameters(tmp_path):
    adapters, converters = sqlite3.adapters.copy(), sqlite3.converters.copy()
    aware = datetime(2026, 10, 30, 12, 34, 56, 123456, timezone(timedelta(hours=8)))
    naive = aware.replace(tzinfo=None)
    day = date(2026, 10, 30)
    path = tmp_path / 'dates.sqlite3'
    with atomic(path) as conn:
        conn.execute('CREATE TABLE dates (day DATE, stamp TIMESTAMP, other DATETIME, optional TEXT)')
        conn.execute('INSERT INTO dates VALUES (?, ?, ?, ?)', (day, aware, naive, None))
        conn.cursor().executemany('INSERT INTO dates VALUES (:day, :stamp, :other, :optional)',
            ({'day': day, 'stamp': aware, 'other': naive, 'optional': None} for _ in range(2)))
        conn.executemany('INSERT INTO dates VALUES (?, ?, ?, ?)', [(day, aware, naive, None)])
        rows = conn.execute('SELECT * FROM dates').fetchall()
        assert len(rows) == 4
        for row in rows:
            assert tuple(row) == (day.isoformat(), aware.isoformat(' '), naive.isoformat(' '), None)
            assert datetime.fromisoformat(row['stamp']) == aware
            assert datetime.fromisoformat(row['other']).tzinfo is None
    # Do not register process-global adapters/converters that affect SDK/other plugins.
    assert sqlite3.adapters == adapters
    assert sqlite3.converters == converters


@pytest.mark.asyncio
async def test_legacy_timestamp_strings_in_domain_repositories(tmp_path):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        # Old SQLite TIMESTAMP/defaults, explicit ISO microseconds and offsets all
        # remain readable without SQLite's default timestamp converter.
        for stamp in ('2026-10-30 12:34:56', '2026-10-30 12:34:56.123456',
                      '2026-10-30 12:34:56.123456+08:00'):
            with atomic(p.db_path) as conn:
                conn.execute('DELETE FROM user_buffs')
                conn.execute('INSERT INTO user_buffs (user_id, buff_type, started_at, expires_at) VALUES (?, ?, ?, ?)',
                             ('12345', 'TEST', stamp, stamp))
                row = conn.execute('SELECT * FROM user_buffs').fetchone()
                buff = p.engine.buff_repo._to_domain(row)
                assert buff.started_at == datetime.fromisoformat(stamp)
                assert buff.expires_at == datetime.fromisoformat(stamp)
                assert isinstance(buff, UserBuff)
        with atomic(p.db_path):
            sent = p.engine.red_packet_service.send_red_packet('12345', 'qq_group_123', 'normal', 100, 1)
            packet = p.engine.red_packet_repo.get_red_packet_by_id(sent['packet_id'])
            assert isinstance(packet.created_at, datetime)
            assert isinstance(packet.expires_at, datetime)
    finally:
        await p.on_unload()


@pytest.mark.parametrize('legacy', [False, True])
def test_logo_background_pixels_and_alpha(monkeypatch, legacy):
    image = Image.new('RGBA', (3, 1))
    pixels = [(255, 255, 255, 0), (240, 240, 240, 128), (239, 250, 250, 255)]
    image.putdata(pixels)
    if legacy:
        monkeypatch.delattr(Image.Image, 'get_flattened_data', raising=False)
    result = replace_white_background(image, (1, 2, 3))
    assert result.mode == 'RGBA'
    assert [result.getpixel((x, 0)) for x in range(3)] == [(1, 2, 3, 0), (1, 2, 3, 128), pixels[2]]
    assert [image.getpixel((x, 0)) for x in range(3)] == pixels


def test_emoji_fallback_and_mixed_composite():
    from PIL import Image, ImageDraw
    from fishing_port.draw.text_utils import load_font_with_cjk_fallback
    font = load_font_with_cjk_fallback('draw/resource/DouyinSansBold.otf', 28)
    assert font.emoji_font is not None
    assert font._get_font_for_char('钓') is font.primary_font
    assert font._get_font_for_char('🎣') is font.emoji_font
    assert font._font_for_text('注册') is font.primary_font  # 纯主字体字符必须走原路径，不得进合成
    assert font._get_font_for_char('️') is font.emoji_font  # 零宽变体符
    mixed = '🎣 基础与核心玩法 💰'
    assert font._font_for_text(mixed) is None  # 混排必须逐字符合成
    assert font.getlength(mixed) > font.getlength('基础与核心玩法')  # emoji 占宽
    image = Image.new('RGB', (600, 60), (255, 255, 255))
    ImageDraw.Draw(image).text((8, 8), mixed, font=font, fill=(0, 0, 0))
    dark = sum(1 for p in image.convert('L').tobytes() if p < 100)
    assert dark > 200  # emoji 与中文都实际落墨


def test_bundled_fonts_declared_cmaps():
    from fontTools.ttLib import TTFont
    emoji = TTFont('draw/resource/NotoEmoji-Regular.ttf', lazy=True).getBestCmap()
    for codepoint in (0x1F3A3, 0x1F4B0, 0x1F9E7, 0x1F3B2, 0x274C, 0x2705, 0xFE0F):
        assert codepoint in emoji
