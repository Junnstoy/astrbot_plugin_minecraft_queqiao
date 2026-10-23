"""Run actual MC entry and AstrBot process stage with local-only endpoints."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
import os, tempfile
_test_root = tempfile.TemporaryDirectory(prefix="mc-host-routing-")
os.environ["ASTRBOT_ROOT"] = _test_root.name

from astrbot_plugin_minecraft_queqiao.main import MinecraftQueQiaoPlugin as Plugin
from astrbot.core.star.star_handler import star_handlers_registry
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.pipeline.process_stage.stage import ProcessStage


class Event:
    unified_msg_origin = 'test:GroupMessage:123456789'
    _has_send_oper = False
    call_llm = False
    def __init__(self, text, wake=True, quoted=False, command=False):
        self.message_str = text
        self.is_at_or_wake_command = wake
        self.quoted = quoted
        self.stopped = False
        self.extra = {'activated_handlers': [NS(event_filters=[CommandFilter('sub_list')] if command else [])]}
    def get_message_str(self): return self.message_str
    def get_messages(self): return [NS(type='Reply')] if self.quoted else []
    def get_extra(self, key): return self.extra.get(key)
    def set_extra(self, key, value): self.extra[key] = value
    def get_result(self): return NS() if self.stopped else None
    def is_stopped(self): return self.stopped
    def stop_event(self): self.stopped = True


async def run():
    metadata = next(h for h in star_handlers_registry if h.handler_module_path == Plugin.__module__ and h.handler_name == 'on_message')
    assert metadata.extras_configs['priority'] < 0
    host = {'wake_prefix': ['#'], 'provider_settings': {'enable': True, 'wake_prefix': 'chat'}}
    plugin = Plugin.__new__(Plugin)
    plugin.context = NS(get_config=lambda **kwargs: host)
    plugin.message_bridge = NS(servers_for_session=lambda _: [])
    forwarded = []
    async def relay(event, umo, text, images):
        forwarded.append(text)
        return True
    plugin._relay_to_minecraft = relay
    plugin._extract_images = lambda event: []

    ai_calls, rss_calls = [], []
    class Plugins:
        async def process(self, event):
            async for value in plugin.on_message(event):
                yield value
            if not event.is_stopped() and event.get_message_str() == 'sub_list':
                rss_calls.append(event.get_message_str())
                event._has_send_oper = True
    class AI:
        async def process(self, event):
            ai_calls.append(event.get_message_str())
            if False:
                yield
    stage = ProcessStage()
    stage.ctx = NS(astrbot_config=host)
    stage.star_request_sub_stage, stage.agent_sub_stage = Plugins(), AI()
    for text, quoted in [('chat 问题', False), ('chat 问题', True), ('chat你好', False)]:
        event = Event(text, quoted=quoted)
        assert [x async for x in stage.process(event)] == []
        assert ai_calls[-1] == text and not event.stopped and not forwarded
    event = Event('sub_list', command=True)
    assert [x async for x in stage.process(event)] == []
    assert rss_calls == ['sub_list'] and not event.stopped and not forwarded
    for text, wake, quoted in [('普通聊天', False, False), ('收到，马上来', True, True), ('ai 问题', False, False), ('chat 问题', False, False)]:
        event = Event(text, wake, quoted)
        before = len(ai_calls)
        assert [x async for x in stage.process(event)] == []
        assert event.stopped and forwarded[-1] == text and len(ai_calls) == before
    # Current-session provider changes, including a provider prefix containing the global prefix.
    host['provider_settings']['wake_prefix'] = '#ask'
    event = Event('ask 问题')
    assert [x async for x in stage.process(event)] == []
    assert ai_calls[-1] == 'ask 问题' and not event.stopped
    print('PASS: low priority; real ProcessStage reaches AI/RSS; quoted AI, host prefix changes, normal/quoted MC forwarding, no new ai entry and no wake bypass')

try:
    asyncio.run(run())
finally:
    _test_root.cleanup()
