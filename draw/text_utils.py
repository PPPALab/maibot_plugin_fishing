"""
文本处理工具函数
优化文本测量、换行和渲染性能
"""
import os
import math
import platform
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFont
from typing import List, Tuple, Optional


def get_text_size_cached(text: str, font: ImageFont.FreeTypeFont, cache: dict = None) -> Tuple[int, int]:
    """
    带缓存的文本尺寸测量，避免重复计算
    
    Args:
        text: 要测量的文本
        font: 字体对象
        cache: 可选的缓存字典
    
    Returns:
        (width, height): 文本尺寸
    """
    if cache is None:
        # 如果没有提供缓存，直接测量
        return _measure_text_size(text, font)
    
    # 使用缓存
    cache_key = f"{text}_{font.size}"
    if cache_key not in cache:
        cache[cache_key] = _measure_text_size(text, font)
    
    return cache[cache_key]


def _measure_text_size(text: str, font: ImageFont.FreeTypeFont) -> Tuple[int, int]:
    """
    测量文本尺寸的内部函数
    """
    # 创建临时图像进行测量
    temp_img = Image.new('RGB', (1, 1), (255, 255, 255))
    temp_draw = ImageDraw.Draw(temp_img)
    bbox = temp_draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def wrap_text_by_width_optimized(text: str, font: ImageFont.FreeTypeFont, max_width: int, cache: dict = None) -> List[str]:
    """
    优化的文本按宽度换行函数
    
    Args:
        text: 要换行的文本
        font: 字体对象
        max_width: 最大宽度
        cache: 可选的缓存字典
    
    Returns:
        List[str]: 换行后的文本行列表
    """
    if not text:
        return []
    
    # 如果文本很短，直接返回
    text_width, _ = get_text_size_cached(text, font, cache)
    if text_width <= max_width:
        return [text]
    
    lines = []
    current_line = ""
    
    # 按字符分割，但优化测量频率
    for char in text:
        test_line = current_line + char
        test_width, _ = get_text_size_cached(test_line, font, cache)
        
        if test_width <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = char
    
    if current_line:
        lines.append(current_line)
    
    return lines


def wrap_text_by_width_with_hyphenation(text: str, font: ImageFont.FreeTypeFont, max_width: int, cache: dict = None) -> List[str]:
    """
    带连字符的文本换行，适用于英文文本
    
    Args:
        text: 要换行的文本
        font: 字体对象
        max_width: 最大宽度
        cache: 可选的缓存字典
    
    Returns:
        List[str]: 换行后的文本行列表
    """
    if not text:
        return []
    
    # 先尝试简单换行
    lines = wrap_text_by_width_optimized(text, font, max_width, cache)
    
    # 如果只有一行，直接返回
    if len(lines) <= 1:
        return lines
    
    # 对每行进行连字符优化
    optimized_lines = []
    for line in lines:
        if len(line) > 10 and ' ' in line:  # 只对较长的行进行连字符处理
            words = line.split(' ')
            if len(words) > 1:
                # 尝试在单词边界换行
                current_line = ""
                for word in words:
                    test_line = current_line + (" " if current_line else "") + word
                    test_width, _ = get_text_size_cached(test_line, font, cache)
                    
                    if test_width <= max_width:
                        current_line = test_line
                    else:
                        if current_line:
                            optimized_lines.append(current_line)
                        current_line = word
                
                if current_line:
                    optimized_lines.append(current_line)
            else:
                optimized_lines.append(line)
        else:
            optimized_lines.append(line)
    
    return optimized_lines


def create_text_cache() -> dict:
    """
    创建文本测量缓存
    
    Returns:
        dict: 空的缓存字典
    """
    return {}


def clear_text_cache(cache: dict) -> None:
    """
    清空文本测量缓存
    
    Args:
        cache: 要清空的缓存字典
    """
    cache.clear()


def get_text_metrics_batch(texts: List[str], font: ImageFont.FreeTypeFont, cache: dict = None) -> List[Tuple[int, int]]:
    """
    批量测量文本尺寸，提高效率
    
    Args:
        texts: 文本列表
        font: 字体对象
        cache: 可选的缓存字典
    
    Returns:
        List[Tuple[int, int]]: 每个文本的尺寸列表
    """
    if cache is None:
        return [_measure_text_size(text, font) for text in texts]
    
    results = []
    for text in texts:
        results.append(get_text_size_cached(text, font, cache))
    
    return results


def _find_cjk_font() -> Optional[str]:
    """
    查找CJK字体路径（支持繁体中文字符）
    
    Returns:
        字体文件路径，如果找不到则返回None
    """
    resource_dir = os.path.join(os.path.dirname(__file__), "resource")
    
    # 使用项目资源目录中的字体（按优先级排序）
    cjk_fonts = [
        "NotoSansTC-Bold.ttf",  # Noto Sans 繁体中文（优先）
        "NotoSansJP-Bold.ttf",  # Noto Sans 日文（后备）
    ]
    
    for font_name in cjk_fonts:
        font_path = os.path.join(resource_dir, font_name)
        if os.path.exists(font_path):
            return font_path
    
    return None


@lru_cache(maxsize=8)
def _font_codepoints(path):
    from fontTools.ttLib import TTFont
    try:
        with TTFont(path, lazy=True) as font:
            return frozenset(font.getBestCmap())
    except Exception:
        return frozenset()


class FontWithFallback(ImageFont.FreeTypeFont):
    """
    带自动回退的字体包装类
    当主字体不支持某个字符时，自动使用CJK字体；emoji回退到Noto Emoji
    """
    def __init__(self, primary_font: ImageFont.FreeTypeFont, fallback_font: Optional[ImageFont.FreeTypeFont] = None,
                 emoji_font: Optional[ImageFont.FreeTypeFont] = None):
        self.primary_font = primary_font
        self.fallback_font = fallback_font
        self.emoji_font = emoji_font
        self._char_cache = {}  # 缓存字符到字体的映射
        self.codepoints = _font_codepoints(getattr(primary_font, 'path', ''))
        # cmap 必须挂在字体对象自身，供 _font_covering_all 按字体查询
        primary_font.codepoints = self.codepoints
        for font in (fallback_font, emoji_font):
            if font is not None:
                font.codepoints = _font_codepoints(getattr(font, 'path', ''))
    
    def _is_cjk_char(self, char: str) -> bool:
        """判断是否为CJK字符（中文、日文、韩文）"""
        if not char:
            return False
        code = ord(char)
        # CJK统一汉字、CJK扩展A/B/C/D/E、CJK兼容汉字、日文平假名/片假名、韩文等
        return (
            0x4E00 <= code <= 0x9FFF or  # CJK统一汉字
            0x3400 <= code <= 0x4DBF or  # CJK扩展A
            0x20000 <= code <= 0x2A6DF or  # CJK扩展B
            0x2A700 <= code <= 0x2B73F or  # CJK扩展C
            0x2B740 <= code <= 0x2B81F or  # CJK扩展D
            0x2B820 <= code <= 0x2CEAF or  # CJK扩展E
            0xF900 <= code <= 0xFAFF or  # CJK兼容汉字
            0x3040 <= code <= 0x309F or  # 日文平假名
            0x30A0 <= code <= 0x30FF or  # 日文片假名
            0xAC00 <= code <= 0xD7AF     # 韩文音节
        )
    
    def _get_font_for_char(self, char: str) -> ImageFont.FreeTypeFont:
        """
        选择字符的渲染字体
        
        策略：
        1. 主字体 cmap 覆盖 → 主字体
        2. CJK 回退字体覆盖 → CJK 回退
        3. emoji（含VS16/ZWJ零宽符）→ Noto Emoji
        4. mask 异常兜底 → CJK 回退或主字体
        """
        if char in self._char_cache:
            return self._char_cache[char]
        # Missing glyphs often render a non-empty tofu box; mask/bbox alone cannot
        # detect them. Use the actual cmap, retaining upstream font where supported.
        code = ord(char) if char else 0
        if not (self.codepoints and code in self.codepoints):
            if self.fallback_font and getattr(self.fallback_font, 'codepoints', None) \
                    and code in self.fallback_font.codepoints:
                self._char_cache[char] = self.fallback_font
                return self.fallback_font
            if self.emoji_font and getattr(self.emoji_font, 'codepoints', None) \
                    and code in self.emoji_font.codepoints:
                self._char_cache[char] = self.emoji_font
                return self.emoji_font
        
        try:
            mask = self.primary_font.getmask(char)
            
            # mask 为空说明不支持
            if mask.size[0] == 0 or mask.size[1] == 0:
                font = self.fallback_font if self.fallback_font else self.primary_font
                self._char_cache[char] = font
                return font
            
            # CJK 字符：检查 bbox 是否有效
            if self._is_cjk_char(char):
                bbox = mask.getbbox()
                if bbox is None or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                    font = self.fallback_font if self.fallback_font else self.primary_font
                    self._char_cache[char] = font
                    return font
            
            # 主字体支持
            self._char_cache[char] = self.primary_font
            return self.primary_font
            
        except Exception:
            # 异常时使用回退字体
            font = self.fallback_font if self.fallback_font else self.primary_font
            self._char_cache[char] = font
            return font
    
    def _fonts(self):
        for font in (self.primary_font, self.fallback_font, self.emoji_font):
            if font is not None:
                yield font

    def _font_covering_all(self, text):
        for font in self._fonts():
            cps = getattr(font, 'codepoints', None)
            if cps and all(ord(c) in cps for c in text):
                return font
        return None

    def _font_for_text(self, text):
        return self._font_covering_all(text)

    def _iter_char_fonts(self, text):
        for char in text:
            yield char, self._get_font_for_char(char)

    def _runs(self, text):
        """Keep adjacent glyphs together for kerning and emoji selectors."""
        runs = []
        for char, font in self._iter_char_fonts(text):
            if runs and runs[-1][1] is font:
                runs[-1] = (runs[-1][0] + char, font)
            else:
                runs.append((char, font))
        return runs

    def _mixed_layout(self, text, mode='', direction=None, features=None,
                      language=None, stroke_width=0, anchor=None):
        anchor = anchor or 'la'
        if direction not in (None, 'ltr') or len(anchor) != 2 or anchor[0] not in 'lmr' or anchor[1] not in 'atmsbd':
            raise ValueError('Mixed fallback supports horizontal ltr text anchors only')
        runs, cursor, bounds = [], 0.0, None
        for part, font in self._runs(text):
            box = font.getbbox(part, mode, direction, features, language, stroke_width, 'ls')
            box = (cursor + box[0], box[1], cursor + box[2], box[3])
            if box[3] > box[1]:
                bounds = box if bounds is None else (min(bounds[0], box[0]), min(bounds[1], box[1]),
                                                     max(bounds[2], box[2]), max(bounds[3], box[3]))
            runs.append((cursor, part, font))
            cursor += font.getlength(part, mode, direction, features, language)
        bounds = bounds or (0, 0, 0, 0)
        dx = {'l': 0, 'm': -round(cursor / 2), 'r': -round(cursor)}[anchor[0]]
        ascent, descent = self.primary_font.getmetrics()
        dy = {'a': ascent, 't': -bounds[1], 'm': (ascent - descent) // 2,
              's': 0, 'b': -bounds[3], 'd': -descent}[anchor[1]]
        box = (math.floor(bounds[0] + dx), math.floor(bounds[1] + dy),
               math.ceil(bounds[2] + dx), math.ceil(bounds[3] + dy))
        return runs, box, dx, dy

    def getmask(self, text, mode="", *args, **kwargs):
        """获取文本的mask，自动处理回退"""
        font = self._font_for_text(text)
        if font is not None:
            return font.getmask(text, mode, *args, **kwargs)
        return self.getmask2(text, mode, *args, **kwargs)[0]
    
    def getbbox(self, text, *args, **kwargs):
        """获取文本边界框"""
        font = self._font_for_text(text)
        if font is not None:
            return font.getbbox(text, *args, **kwargs)
        return self._mixed_layout(text, *args, **kwargs)[1]

    def getlength(self, text, *args, **kwargs):
        font = self._font_for_text(text)
        if font is not None:
            return font.getlength(text, *args, **kwargs)
        return sum(font.getlength(part, *args, **kwargs) for part, font in self._runs(text))

    def getmask2(self, text, mode='', direction=None, features=None, language=None,
                 stroke_width=0, anchor=None, ink=0, start=None, **kwargs):
        font = self._font_for_text(text)
        if font is not None:
            return font.getmask2(text, mode, direction, features, language, stroke_width,
                                 anchor, ink, start=start, **kwargs)
        runs, box, dx, dy = self._mixed_layout(text, mode, direction, features, language, stroke_width, anchor)
        canvas = Image.new('L', (max(0, box[2] - box[0]), max(0, box[3] - box[1])))
        draw = ImageDraw.Draw(canvas)
        for x, part, font in runs:
            draw.text((x + dx - box[0], dy - box[1]), part, font=font, fill=255, anchor='ls',
                      direction=direction, features=features, language=language, stroke_width=stroke_width)
        return canvas.im, box[:2]

    def __getattr__(self, name):
        """代理其他属性到主字体"""
        return getattr(self.primary_font, name)


def load_font_with_cjk_fallback(font_path: str, size: int) -> FontWithFallback:
    """
    加载字体，自动添加CJK回退支持
    
    Args:
        font_path: 主字体文件路径
        size: 字体大小
    
    Returns:
        FontWithFallback: 带回退的字体对象
    """
    # 加载主字体
    try:
        primary_font = ImageFont.truetype(font_path, size)
    except Exception:
        primary_font = ImageFont.load_default()
    
    # 加载CJK字体作为回退（仅使用项目资源中的字体，不查询系统）
    fallback_font = None
    cjk_font_path = _find_cjk_font()
    if cjk_font_path:
        try:
            fallback_font = ImageFont.truetype(cjk_font_path, size)
        except Exception as e:
            # 如果加载失败，记录错误但不抛出异常
            pass
    
    return FontWithFallback(primary_font, fallback_font, _load_emoji_font(size))


EMOJI_FONT_PATH = os.path.join(os.path.dirname(__file__), "resource", "NotoEmoji-Regular.ttf")


@lru_cache(maxsize=16)
def _load_emoji_font(size: int) -> Optional[ImageFont.FreeTypeFont]:
    """加载等宽单色 Noto Emoji（OFL），用于主/CJK字体均缺失的字形。"""
    try:
        font = ImageFont.truetype(EMOJI_FONT_PATH, size)
        try:
            font.set_variation_by_axes([400])
        except Exception:
            pass
        return font
    except Exception:
        return None


def draw_text_smart(
    draw: ImageDraw.Draw,
    position: Tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: Tuple[int, int, int] = (0, 0, 0)
) -> None:
    """
    智能文本绘制函数，自动处理字体回退
    
    如果传入的font是FontWithFallback类型，会自动使用回退字体处理缺失字符
    否则直接使用普通绘制
    
    Args:
        draw: ImageDraw对象
        position: 文本位置 (x, y)
        text: 要绘制的文本
        font: 字体对象（可以是FontWithFallback或普通字体）
        fill: 文本颜色
    """
    # One layout path for measurement and drawing (including fallback advances).
    draw.text(position, text, font=font, fill=fill)
