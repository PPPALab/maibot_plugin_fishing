"""Original offline schematic illustrations, not species photographs.

Created for the MaiBot port; distributed under the project's AGPL-3.0-only.
Never overwrite an administrator's icon_url or change game/database records.
"""
import hashlib

from PIL import Image, ImageDraw


def fish_illustration(name, size=60, rarity=1):
    scale = 4
    image = Image.new('RGBA', (128 * scale, 128 * scale))
    draw = ImageDraw.Draw(image)
    seed = hashlib.sha256(str(name).encode()).digest()
    palette = [(56, 154, 190), (232, 164, 65), (106, 167, 117), (147, 124, 202), (221, 123, 131)]
    color = palette[seed[0] % len(palette)]
    dark = tuple(max(0, c - 55) for c in color)

    def ellipse(box, **kwargs):
        draw.ellipse(tuple(int(v * scale) for v in box), **kwargs)

    def polygon(points, **kwargs):
        draw.polygon([(int(x * scale), int(y * scale)) for x, y in points], **kwargs)

    def line(points, **kwargs):
        draw.line([(int(x * scale), int(y * scale)) for x, y in points], width=2 * scale, **kwargs)

    ellipse((3, 3, 125, 125), fill=(231, 245, 251, 255))
    if any(word in name for word in ('靴', '罐', '石', '水草')):
        if '靴' in name:
            polygon([(40, 26), (76, 26), (74, 71), (104, 80), (102, 98), (35, 98)], fill='#987b64')
            line([(35, 98), (104, 98)], fill='#55493f')
        elif '罐' in name:
            polygon([(36, 33), (90, 33), (86, 97), (40, 97)], fill='#9ba8ad')
            ellipse((36, 25, 90, 43), fill='#687e88')
            line([(47, 51), (79, 51)], fill='#b5c6cc')
        elif '水草' in name:
            for x in (35, 52, 70, 86):
                line([(64, 105), (x, 77), (x + 8, 48), (x, 23)], fill='#4f996c')
        else:
            polygon([(24, 83), (39, 42), (72, 31), (102, 64), (94, 97), (49, 101)], fill='#8b9ba6')
    elif any(word in name for word in ('虾', '龙虾')):
        color = '#df9875'
        for i in range(5):
            ellipse((35 + i * 8, 40 + i * 6, 68 + i * 8, 65 + i * 6), fill=color, outline='#af6b53', width=scale)
        line([(41, 43), (25, 20), (10, 25)], fill='#af6b53')
        line([(44, 45), (37, 18), (25, 15)], fill='#af6b53')
        polygon([(95, 75), (113, 70), (109, 96), (93, 94)], fill=color)
        ellipse((44, 45, 50, 51), fill='#294452')
    elif any(word in name for word in ('鳗', '鳅', '带鱼')):
        points = [(22, 54), (39, 37), (63, 45), (72, 80), (93, 87), (108, 67)]
        draw.line([(x * scale, y * scale) for x, y in points], fill=color, width=17 * scale, joint='curve')
        ellipse((17, 41, 41, 65), fill=color)
        ellipse((22, 46, 28, 52), fill='#243f50')
    else:
        polygon([(78, 63), (115, 35), (110, 91)], fill=dark)
        polygon([(48, 46), (65, 20), (82, 49)], fill=dark)
        ellipse((16, 40, 98, 91), fill=color)
        polygon([(56, 68), (73, 76), (58, 90)], fill=dark)
        ellipse((29, 53, 42, 66), fill='white')
        ellipse((31, 55, 38, 63), fill='#243f50')
        draw.arc((43 * scale, 47 * scale, 60 * scale, 80 * scale), 280, 80, fill=dark, width=2 * scale)
        for i in range(seed[1] % 4):
            ellipse((64 + i * 7, 52, 68 + i * 7, 57), fill=dark)
    return image.resize((size, size), Image.Resampling.LANCZOS)
