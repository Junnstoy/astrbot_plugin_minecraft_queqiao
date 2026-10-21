"""重连性质判定：区分「服务器真的启动过」与「只是鹊桥 WS 抖了一下」。

连通通知（`notify_on_connect`）由连接层的 `on_connect` 触发，而它在断线重连、
插件热重载后同样会走一遍。这让它在两种完全不同的场景下发出同一句文案：

- 服务器真的重启过（关服 → 开机 → 鹊桥重新连入）
- 桥断了、服务器一直在跑（WS keepalive 超时、网络抖动、服务端短暂卡顿）

实测后者会造成误报：断线 5 秒后重连，群里收到「🟢 服务器已启动」，而服务器
其实从没停过。分辨两者只需在**断线期间**对服务器游戏端口做 SLP 探测：

- 探测到服务器仍在响应 → 桥断了、服务器没停 → 抖动，不发通知
- 探测到连不上 → 服务器确实下线过 → 真启动，发通知
- 一次都没探出结论（几秒内就连回，或未配置探测地址）→ 退回断线时长阈值
"""

from __future__ import annotations

import asyncio
import time

from astrbot.api import logger

from ..core.constants import (
    CONNECT_NOTIFY_MIN_DOWNTIME,
    CONNECT_NOTIFY_PROBE_INTERVAL,
    CONNECT_NOTIFY_PROBE_MAX,
    CONNECT_NOTIFY_PROBE_TIMEOUT,
    PLUGIN_NAME,
)
from .slp_ping import mc_ping

# 判定依据，仅用于日志（便于事后解释「这次为什么没发通知」）
VERDICT_FIRST_CONNECT = "first_connect"
VERDICT_SERVER_DOWN = "slp_down"
VERDICT_SERVER_ALIVE = "slp_alive"
VERDICT_DOWNTIME = "downtime"


class ConnectWatch:
    """按服务器记录断线时刻与断线期间的服务端可达性，给出「是否真启动」结论。"""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lost_at: dict[str, float] = {}
        self._down: set[str] = set()
        self._alive: set[str] = set()

    # ---- 断线侧 ----

    def outage_started(self, server_name: str, target: tuple[str, int] | None) -> None:
        """登记一次断线，并在配置了探测地址时启动后台探测。"""
        self._cancel(server_name)
        self._lost_at[server_name] = time.monotonic()
        self._down.discard(server_name)
        self._alive.discard(server_name)
        if target is None:
            return
        host, port = target
        self._tasks[server_name] = asyncio.create_task(
            self._probe(server_name, host, port)
        )

    async def _probe(self, server_name: str, host: str, port: int) -> None:
        try:
            for _ in range(CONNECT_NOTIFY_PROBE_MAX):
                ping = await mc_ping(
                    host, port, timeout=CONNECT_NOTIFY_PROBE_TIMEOUT
                )
                if ping is None:
                    # 一旦连不上就锁定：服务器随后启动、后续探测恢复可达也不改判，
                    # 否则真重启会被「后半程探到服务器在跑」翻成抖动
                    self._down.add(server_name)
                    return
                self._alive.add(server_name)
                await asyncio.sleep(CONNECT_NOTIFY_PROBE_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 探测失败不能影响连接本身
            logger.warning(
                f"[{PLUGIN_NAME}][{server_name}] ⚠️ 断线探测异常: {exc}"
            )

    # ---- 重连侧 ----

    def verdict(self, server_name: str) -> tuple[bool, str, float]:
        """返回 (是否真启动, 判定依据, 本次断线时长秒)。

        首次连接（插件启动后第一次连上，无断线记录）按真启动处理，与改造前
        的行为一致：插件刚加载完成时报一次「服务器已启动」是用户预期。
        """
        self._cancel(server_name)
        lost_at = self._lost_at.pop(server_name, None)
        down = server_name in self._down
        alive = server_name in self._alive
        self._down.discard(server_name)
        self._alive.discard(server_name)
        if lost_at is None:
            return True, VERDICT_FIRST_CONNECT, 0.0
        downtime = max(0.0, time.monotonic() - lost_at)
        if down:
            return True, VERDICT_SERVER_DOWN, downtime
        if alive:
            return False, VERDICT_SERVER_ALIVE, downtime
        return downtime >= CONNECT_NOTIFY_MIN_DOWNTIME, VERDICT_DOWNTIME, downtime

    def close(self) -> None:
        """取消全部探测任务（插件卸载/停止时调用）。"""
        for server_name in list(self._tasks):
            self._cancel(server_name)

    def _cancel(self, server_name: str) -> None:
        task = self._tasks.pop(server_name, None)
        if task is not None and not task.done():
            task.cancel()
