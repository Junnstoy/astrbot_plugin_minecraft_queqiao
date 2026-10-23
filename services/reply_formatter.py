"""可配置回复模板及本地 Python 回复函数；不改变指令权限或执行结果。"""
import inspect
import re
from pathlib import Path

from astrbot.api import logger

from ..core.constants import PLUGIN_NAME
from ..core.models_config import ReplyConfig
STARTER_HOOK = '''"""编辑后下一次回复自动加载，无需重启。
key: 回复类型，如 bind_usage / bind_success / command_success。
data: sender_name、sender_id、player；指令回复还有 command、output、server_name。
text: GUI 模板已替换占位符后的文本。
返回 str 覆盖回复；返回 None 使用模板。只处理文本，不执行游戏指令。
"""


def customize_reply(key, data, text):
    # 示例：去掉指令执行成功的前缀，只保留服务器返回内容。
    # if key == "command_success":
    #     return data.get("output") or "完成啦"
    # if key == "bind_usage":
    #     return "请告诉我你的游戏名，例如：#mc bind Steve"
    return None
'''


class ReplyFormatter:
    def __init__(self, config_getter, hook_path: Path | None = None):
        self.config_getter = config_getter
        self.hook_path = hook_path
        self._stamp = None
        self._hook = None

    def ensure_hook_file(self) -> None:
        if self.hook_path is not None:
            self.hook_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with self.hook_path.open('x', encoding='utf-8') as handle:
                    handle.write(STARTER_HOOK)
            except FileExistsError:
                pass  # 用户脚本始终保留，不覆盖。

    def _load_hook(self):
        if self.hook_path is None or not self.hook_path.exists():
            self._hook = None
            self._stamp = None
            return None
        stat = self.hook_path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp != self._stamp:
            self._stamp = stamp
            self._hook = None
            namespace = {"__name__": "minecraft_reply_hook", "__file__": str(self.hook_path)}
            # 仅加载固定数据目录中、由本机用户编辑的受信任 Python 文件。
            exec(compile(self.hook_path.read_text(encoding='utf-8-sig'), str(self.hook_path), 'exec'), namespace)
            hook = namespace.get('customize_reply')
            if not callable(hook) or inspect.iscoroutinefunction(hook):
                raise TypeError('需要同步函数 customize_reply(key, data, text)')
            self._hook = hook
        return self._hook

    def render(self, key: str, **data) -> str:
        config = ReplyConfig.from_dict(self.config_getter() or {})
        template = config.reply_templates[key]
        # 单次替换：正文中的花括号不会再次求值；未知占位符原样保留。
        text = re.sub(r'\{([A-Za-z_]\w*)\}', lambda match: str(data.get(match[1], match[0])), template).rstrip()
        if config.reply_hook_enabled:
            try:
                hook = self._load_hook()
                result = hook(key, dict(data), text) if hook else None
                if isinstance(result, str) and result.strip():
                    return result
                if result is not None:
                    if inspect.iscoroutine(result):
                        result.close()
                    raise TypeError('回复函数应返回非空字符串或 None')
            except Exception as exc:
                logger.warning(f'[{PLUGIN_NAME}] 自定义回复 {key} 失败，使用模板: {exc}')
        return text
