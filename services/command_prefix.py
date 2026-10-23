"""切换本插件内置指令的父前缀，保留 AstrBot 管理行为与权限过滤器。"""
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter
from astrbot.core.star.star_handler import star_handlers_registry

COMMAND_HANDLERS = {
    "cmd_help", "cmd_status", "cmd_list", "cmd_player", "cmd_execute",
    "cmd_say", "cmd_bind", "cmd_unbind", "cmd_servers", "cmd_rebind",
}


def configure_command_prefix(module_path: str, enabled: bool) -> None:
    handlers = [item for item in star_handlers_registry if item.handler_module_path == module_path]
    group = next((item for item in handlers if item.handler_name == "mc_group"), None)
    children = [item for handler in handlers if handler.handler_name in COMMAND_HANDLERS
                for item in handler.event_filters if isinstance(item, CommandFilter)]
    parents = ["mc"]
    if group is not None:
        # 空 event_filters 会被宿主跳过，也不会出现在管理行为的指令列表中。
        if not hasattr(group, "_mc_prefix_filters"):
            group._mc_prefix_filters = list(group.event_filters)
        for item in group._mc_prefix_filters:
            if isinstance(item, CommandGroupFilter):
                parents = item.get_complete_command_names()
                item.sub_command_filters = children
        group.event_filters = list(group._mc_prefix_filters) if enabled else []
    for handler in handlers:
        if handler.handler_name not in COMMAND_HANDLERS:
            continue
        # 始终以普通指令描述，切换时管理行为记录仍按相同 handler ID 保存。
        handler.extras_configs.pop("sub_command", None)
        for item in handler.event_filters:
            if isinstance(item, CommandFilter):
                item.parent_command_names = list(parents) if enabled else [""]
                item._cmpl_cmd_names = None
