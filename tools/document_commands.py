"""Generate the command audit appendix from source-derived metadata."""
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    commands = json.loads((root / 'commands.json').read_text())
    lines = ['# 实际源码命令清单', '',
             '源于固定上游SHA的main.py装饰器；含所有中文别名与管理员标记。',
             '这些入口已做无参数冒烟，状态均为**已实现未真实QQ测试**。',
             '此清单不是所有参数/玩法分支已经完整测试的声明。', '',
             '| 命令 | 别名 | 权限 | 原handler入口 | 上游说明 |',
             '|---|---|---|---|---|']
    for command in commands:
        aliases = command['aliases']
        description = command['description'].replace('|', '\\|').replace('\n', ' ')
        lines.append(f"| {aliases[0]} | {'、'.join(aliases[1:]) or '—'} | {'管理员' if command['admin'] else '玩家'} | `{command['method']}` | {description} |")
    lines += ['', '## 原源码已实现但main未注册的入口', '',
              '本移植额外连接现成handler：使用道具、使用鱼饵、精炼帮助、水族箱帮助、骰子。',
              '未注册README中尚无业务实现的盈亏/推荐/风险等未来或残留帮助项。', '',
              '交易所的开户、买入、卖出、历史、分析、状态等是 `交易所` 内的子命令，',
              '多十连由 `十连` 参数触发，原handlers解析逻辑保留。', '']
    (root / 'docs/COMMANDS.md').write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
