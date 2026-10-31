"""Pixel/bounds regressions for the reported missing icons and cut-off text."""
import asyncio

import pytest
from PIL import Image, ImageDraw, ImageChops

from fishing_port.draw.styles import load_font


@pytest.mark.parametrize('anchor', ['la', 'mt', 'mm', 'lm', 'ra'])
def test_wrapped_primary_font_preserves_pixels_and_anchor(anchor):
    font = load_font(24)
    actual = Image.new('L', (400, 140))
    expected = actual.copy()
    ImageDraw.Draw(actual).text((200, 60), '更新昵称 [新昵称]', font=font, fill=255, anchor=anchor)
    ImageDraw.Draw(expected).text((200, 60), '更新昵称 [新昵称]', font=font.primary_font, fill=255, anchor=anchor)
    assert ImageChops.difference(actual, expected).getbbox() is None


@pytest.mark.parametrize('anchor', ['la', 'mt', 'mm', 'lm', 'ra'])
def test_mixed_font_ink_matches_measured_bounds(anchor):
    font = load_font(24)
    image = Image.new('L', (600, 180))
    draw = ImageDraw.Draw(image)
    text = '🎣 基础与核心玩法 ⚠️'
    box = draw.textbbox((300, 90), text, font=font, anchor=anchor)
    draw.text((300, 90), text, font=font, anchor=anchor, fill=255)
    ink = image.getbbox()
    assert ink is not None
    # FreeType bounds include up to 3px side bearing around the fishing emoji.
    assert all(abs(a - b) <= 4 for a, b in zip(box, ink)), (box, ink)


def test_pokedex_draws_icon_even_without_upstream_url(monkeypatch, tmp_path):
    from fishing_port.draw import pokedex
    calls = []

    async def icon(url, directory, size, **kwargs):
        calls.append((url, kwargs))
        return Image.new('RGBA', (size, size), 'red')

    monkeypatch.setattr(pokedex, 'get_fish_icon', icon)
    data = {'pokedex': [{'name': '小鲫鱼', 'icon_url': None, 'rarity': 1}]}
    asyncio.run(pokedex.draw_pokedex(data, {}, str(tmp_path / 'fish.png'), data_dir=str(tmp_path)))
    assert len(calls) == 1


def test_help_text_stays_inside_cards(monkeypatch):
    from fishing_port.draw.help import draw_help_image
    cards = []
    original_card = ImageDraw.ImageDraw.rounded_rectangle
    original_text = ImageDraw.ImageDraw.text

    def card(self, xy, *args, **kwargs):
        if kwargs.get('outline') == (200, 200, 200):
            cards.append(tuple(xy))
        return original_card(self, xy, *args, **kwargs)

    def text(self, xy, value, *args, **kwargs):
        if kwargs.get('fill') in ((40, 40, 40), (100, 100, 100)):
            bounds = self.textbbox(xy, value, font=kwargs['font'], anchor=kwargs.get('anchor'))
            left, top, right, bottom = cards[-1]
            assert left + 5 <= bounds[0] <= bounds[2] <= right - 5, (value, bounds, cards[-1])
            assert top + 5 <= bounds[1] <= bounds[3] <= bottom - 5, (value, bounds, cards[-1])
        return original_text(self, xy, value, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, 'rounded_rectangle', card)
    monkeypatch.setattr(ImageDraw.ImageDraw, 'text', text)
    draw_help_image()


def test_help_does_not_draw_upstream_brand(monkeypatch):
    from fishing_port.draw.help import draw_help_image
    original_open = Image.open
    original_text = ImageDraw.ImageDraw.text

    def opened(path, *args, **kwargs):
        assert 'astrbot' not in str(path).split('/')[-1].lower()
        return original_open(path, *args, **kwargs)

    def text(self, xy, value, *args, **kwargs):
        assert 'astrbot' not in str(value).lower()
        return original_text(self, xy, value, *args, **kwargs)

    monkeypatch.setattr(Image, 'open', opened)
    monkeypatch.setattr(ImageDraw.ImageDraw, 'text', text)
    draw_help_image()


def test_all_default_fish_have_offline_illustrations(tmp_path, monkeypatch):
    from fishing_port import media
    from fishing_port.core.initial_data import FISH_DATA
    from fishing_port.draw.fish_art import fish_illustration

    async def no_network(url):
        raise AssertionError('Default fish art must not require network')

    monkeypatch.setattr(media, 'download', no_network)
    for name, _, rarity, *rest in FISH_DATA:
        icon = asyncio.run(media.get_fish_icon(None, str(tmp_path), fish_name=name, rarity=rarity))
        assert icon.size == (60, 60)
        assert icon.tobytes() == fish_illustration(name, 60, rarity).tobytes()
        assert len(set(icon.tobytes())) > 50


def test_custom_icon_failure_uses_offline_art(tmp_path, monkeypatch):
    from fishing_port import media
    from fishing_port.draw.fish_art import fish_illustration

    async def unavailable(url):
        raise OSError('offline')

    monkeypatch.setattr(media, 'download', unavailable)
    image = asyncio.run(media.get_fish_icon('https://example.org/fish.png', str(tmp_path), fish_name='泥鳅'))
    assert image.tobytes() == fish_illustration('泥鳅').tobytes()
