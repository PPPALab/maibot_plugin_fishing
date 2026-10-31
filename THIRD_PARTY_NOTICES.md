# 来源、版权及资源许可

## 游戏源码

Copyright 原作者及所有上游贡献者（sakuradairong/astrbot_plugin_fishing）。
该固定源码的 `metadata.yaml` 明确记载原作者 **xxlemon**，原repo为
`https://github.com/xxlemon-io/astrbot_plugin_fishing`，版本2.4.7、显示名Fishing Another!；
这些原作者标识与来源均保留。本次获取与业务对照使用的是用户指定的sakuradairong仓库，
不是声称fork持有人是全部源码的唯一原创作者。
来源：https://github.com/sakuradairong/astrbot_plugin_fishing
固定 main：`22e09e9359abfecc5ba450f1d971c79e8962dec2`。
完整上游 Git 历史和原版权声明保留；`LICENSE` 是未经修改的 AGPL-3.0。
本移植修改也以 AGPL-3.0 发布。不得将本目录作为旧 fishing2/MIT 作品分发。
通过网络提供修改版服务时，应遵守 AGPL 第13条提供对应源代码。
原文档可在固定来源提交查阅：
[README](https://github.com/sakuradairong/astrbot_plugin_fishing/blob/22e09e9359abfecc5ba450f1d971c79e8962dec2/README.md)、
[CHANGELOG](https://github.com/sakuradairong/astrbot_plugin_fishing/blob/22e09e9359abfecc5ba450f1d971c79e8962dec2/CHANGELOG.md)、
[metadata](https://github.com/sakuradairong/astrbot_plugin_fishing/blob/22e09e9359abfecc5ba450f1d971c79e8962dec2/metadata.yaml)。
当前根README与CHANGELOG为移植版文档。

移植版发布仓库：https://github.com/PPPALab/maibot_plugin_fishing 。
本项目版本与原AstrBot版本2.4.7相互独立；保留上游作者和版权并不意味着
声称原作者参与或背书本项目的发布。软件许可证继续使用AGPL-3.0-only。

## 字体（不是 AGPL）

- `draw/resource/NotoSansTC-Bold.ttf` 与 `NotoSansJP-Bold.ttf`：字体内 name table
  明确记载 `(c) 2014-2021 Adobe (http://www.adobe.com/), with Reserved Font Name 'Source'.`，
  SIL Open Font License 1.1。附 `draw/resource/OFL-Noto.txt`。
  官方许可：https://github.com/notofonts/noto-cjk/blob/main/Sans/LICENSE
- `draw/resource/NotoEmoji-Regular.ttf`：Google Noto Emoji（可变字重，等宽单色），
  SIL Open Font License 1.1，Copyright 2013 Google LLC。附 `draw/resource/OFL-NotoEmoji.txt`。
  来源：https://github.com/google/fonts/tree/main/ofl/notoemoji
  用于主字体与CJK字体均缺失的字形（主要是emoji符号）的渲染回退。
- `draw/resource/DouyinSansBold.otf`：上游原文件保留，内部版权为北京北大方正电子有限公司；
  内部许可字段仍指向旧的方正授权网页。官方 ByteDance 同名字体仓库将 DouyinSans
  以 SIL OFL 1.1 发布（2023 Beijing Zitiao Network Technology Co. Ltd.，保留名称
  Douyin/抖音/抖音美好），见附 `draw/resource/OFL-Douyin.txt`。
  官方来源：https://github.com/bytedance/fonts/tree/main/DouyinSans 。

## 图片

保留上游 `logo.png`、AstrBot logo资源文件、gold/silver/bronze 图供来源追溯；
帮助页已不再读取或展示 AstrBot logo。上述资源沿用上游分发内容与版权声明，
不得暗示 AstrBot/MaiBot 官方为移植背书。
没有新增完整鱼种实拍图库；图鉴优先使用每条鱼的 `icon_url`，链接图片版权归提供者。
无URL或下载失败使用 `draw/fish_art.py` 本地绘制的风格化示意插画，涵盖普通鱼形、
鳗鳅、虾和垃圾类别，不声称为鱼种的科学形态图或照片。
这些插画代码为本次移植新增，随项目以AGPL-3.0-only发布；无外部素材，无LLM生图。
