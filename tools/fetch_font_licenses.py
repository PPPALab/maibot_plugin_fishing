"""Fetch verbatim upstream font licenses, once, for redistribution notices."""
from pathlib import Path
from urllib.request import urlopen

SOURCES = {
    'OFL-Douyin.txt': 'https://raw.githubusercontent.com/bytedance/fonts/main/DouyinSans/OFL.txt',
    'OFL-Noto.txt': 'https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/LICENSE',
    'OFL-NotoEmoji.txt': 'https://raw.githubusercontent.com/google/fonts/main/ofl/notoemoji/OFL.txt',
}


def main():
    root = Path(__file__).resolve().parents[1] / 'draw/resource'
    for name, url in SOURCES.items():
        with urlopen(url, timeout=30) as response:
            data = response.read(100000)
        with (root / name).open('xb') as output:
            output.write(data)
        print(name)


if __name__ == '__main__':
    main()
