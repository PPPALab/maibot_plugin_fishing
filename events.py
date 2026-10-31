"""Framework-independent game input/output values, constructed from Host payloads."""
from dataclasses import dataclass
from types import SimpleNamespace


@dataclass(frozen=True)
class Mention:
    qq: str


@dataclass(frozen=True)
class Reply:
    kind: str
    content: str
    fallback: str = "图片发送失败，请稍后重试。"


class GameEvent:
    def __init__(self, *, text, user_id, stream_id, group_id='', platform='qq', message=None, admin=False):
        if platform.lower() != 'qq' or not str(user_id).isdecimal() or not stream_id:
            raise ValueError('仅支持 Host 认证的 QQ 账号与有效聊天流')
        message = message or {}
        info = message.get('message_info', {}).get('user_info', {})
        if info.get('user_id') and str(info['user_id']) != str(user_id):
            raise ValueError('Host 身份字段不一致')
        self.user_id = str(user_id)
        self.nickname = str(info.get('user_cardname') or info.get('user_nickname') or user_id)
        self.session_id = self.unified_msg_origin = str(stream_id)
        self.group_id = str(group_id)
        self.platform_meta = SimpleNamespace(platform_name='qq')
        self._admin = bool(admin)
        self.message_id = str(message.get('message_id') or '')
        self.image_fallback = None
        self_id = str(message.get('account_id') or '')
        mentions = []
        # Reconstruct arguments from trusted typed segments, not processed nickname text.
        parts = []
        for segment in message.get('raw_message', []):
            if segment.get('type') == 'text' and isinstance(segment.get('data'), str):
                parts.append(segment['data'])
            elif segment.get('type') == 'at' and isinstance(segment.get('data'), dict):
                target = str(segment['data'].get('target_user_id') or '')
                if target.isdecimal() and target != self_id:
                    mentions.append(Mention(target))
                    parts.append(f' {target} ')
        self.message_obj = SimpleNamespace(message=mentions, self_id=self_id)
        self.message_str = ' '.join((''.join(parts) if parts else text).strip().lstrip('/').split())

    def get_sender_id(self):
        return self.user_id

    def get_sender_name(self):
        return self.nickname

    def get_group_id(self):
        return self.group_id

    def is_admin(self):
        return self._admin

    def plain_result(self, text):
        return Reply('text', str(text))

    def image_result(self, path, fallback=None):
        if fallback is None and self.image_fallback:
            fallback = self.image_fallback()
        return Reply('image', str(path), fallback or '图片发送失败，请稍后重试。')
