"""账号绑定服务：维护外部平台账号 ↔ MC 游戏 ID 的映射。

持久化要求（遵循项目规范）：写操作持锁、utf-8 编码、原子写入（先写 .tmp 再 replace）。
"""

import asyncio
import json
import re
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..core.constants import PLUGIN_NAME


class BindingService:
    """绑定关系的读写与持久化。"""

    def __init__(self, data_dir: Path) -> None:
        self._file = data_dir / "bindings.json"
        self._lock = asyncio.Lock()
        # {umo: {game_id: str}}
        self._bindings: dict[str, str] = {}

    @staticmethod
    def account_key(event: AstrMessageEvent) -> str:
        """按平台实例与发送者隔离绑定，避免同群用户覆盖彼此。"""
        return f"{event.get_platform_id()}:User:{event.get_sender_id()}"

    @staticmethod
    def valid_game_id(game_id: str) -> bool:
        """仅接受 Java 玩家名，避免绑定内容成为命令选择器或参数。"""
        return re.fullmatch(r"[A-Za-z0-9_]{1,16}", game_id) is not None

    def all_bindings(self) -> dict[str, str]:
        """返回绑定快照供管理面板展示。"""
        return dict(self._bindings)

    def load(self) -> None:
        """从磁盘加载绑定关系，文件损坏时以空表继续。"""
        if not self._file.exists():
            return
        try:
            raw = self._file.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError) as exc:
            logger.error(f"[{PLUGIN_NAME}] 读取绑定文件失败，将使用空绑定表: {exc}")
            return

        if isinstance(data, dict):
            self._bindings = {
                str(key): str(value) for key, value in data.items() if value
            }
            logger.info(f"[{PLUGIN_NAME}] 已加载 {len(self._bindings)} 条绑定关系")

    def _save(self) -> None:
        """原子写入：先落临时文件再替换，避免写入中断损坏数据。"""
        tmp = self._file.with_suffix(".json.tmp")
        try:
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(self._bindings, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self._file)
        except OSError as exc:
            logger.error(f"[{PLUGIN_NAME}] 保存绑定文件失败: {exc}")

    async def bind(self, umo: str, game_id: str) -> None:
        """绑定账号与游戏 ID（重复绑定会覆盖）。"""
        if not self.valid_game_id(game_id):
            raise ValueError("游戏 ID 必须为 1–16 位英文字母、数字或下划线")
        async with self._lock:
            self._bindings[umo] = game_id
            self._save()

    async def unbind(self, umo: str) -> bool:
        """解除绑定，返回是否原本存在绑定。"""
        async with self._lock:
            if umo not in self._bindings:
                return False
            del self._bindings[umo]
            self._save()
            return True

    async def bind_if_unbound(self, umo: str, game_id: str) -> str:
        """首次绑定；返回已有游戏名表示未写入，在锁内避免并发覆盖。"""
        if not self.valid_game_id(game_id):
            raise ValueError("游戏 ID 不合法")
        async with self._lock:
            previous = self._bindings.get(umo, "")
            if previous:
                return previous
            self._bindings[umo] = game_id
            self._save()
            return ""

    async def replace_existing(self, umo: str, game_id: str) -> str:
        """管理员更新已有绑定，返回旧游戏名；不存在则不写入。"""
        if not self.valid_game_id(game_id):
            raise ValueError("游戏 ID 不合法")
        async with self._lock:
            previous = self._bindings.get(umo, "")
            if previous:
                self._bindings[umo] = game_id
                self._save()
            return previous

    def get(self, umo: str) -> str:
        """获取某账号绑定的游戏 ID，未绑定时返回空串。"""
        return self._bindings.get(umo, "")
