"""将 QQ 链接与引用转换为原版 Minecraft 可交互文本组件。"""

import re
from string import Formatter
from urllib.parse import urlsplit

URL_PATTERN = re.compile(r"https?://[^\s<>\"\[\]]+")
COLORS = dict(zip("0123456789abcdef", (
    "black", "dark_blue", "dark_green", "dark_aqua", "dark_red", "dark_purple",
    "gold", "gray", "dark_gray", "blue", "green", "aqua", "red", "light_purple", "yellow", "white")))
DECORATIONS = dict(zip("klmno", ("obfuscated", "bold", "strikethrough", "underlined", "italic")))
STYLE_CODES = {name: code for code, name in {**COLORS, **DECORATIONS, "r": "reset"}.items()}
COLOR_PATTERN = re.compile(r"[&§](#[0-9a-fA-F]{6}|[0-9a-fk-orA-FK-OR])")


def format_broadcast(template: str, values: dict[str, str], color: str, target: str = "") -> list[dict]:
    """只解析模板中的颜色，不把 QQ 正文/网址里的 & 或占位符当作模板。"""
    base = {"color": color, **{name: False for name in DECORATIONS.values()}}
    style = dict(base)
    parts: list[dict] = []
    tokens = list(Formatter().parse(template))
    explicit_reply = any(field == "reply" for _, field, _, _ in tokens)

    def append(text: str, override: dict | None = None) -> None:
        if text:
            parts.append({"text": text, **style, **(override or {})})

    def literal(text: str) -> None:
        position = 0
        for match in COLOR_PATTERN.finditer(text):
            append(text[position:match.start()])
            code = match.group(1).lower()
            if code in COLORS or code.startswith("#"):
                style.update(base)
                style["color"] = COLORS.get(code, code)
            elif code == "r":
                style.update(base)
            else:
                style[DECORATIONS[code]] = True
            position = match.end()
        append(text[position:])

    def reply(legacy: bool = False) -> None:
        if target:
            append(f"(回复 @{target})", {**base, "color": "gray"} if legacy else None)

    for text, field, spec, conversion in tokens:
        literal(text)
        if field is None:
            continue
        if field in STYLE_CODES:
            literal("&" + STYLE_CODES[field])
        elif field == "reply":
            reply()
        else:
            value = target if field == "reply_player" else values.get(field, "{" + field + "}")
            if conversion:
                value = Formatter().convert_field(value, conversion)
            append(format(value, spec))
            if field in {"sender", "player"} and not explicit_reply:
                reply(legacy=True)
    return parts


def quote_hover(reference: str, link: str = "") -> dict:
    """引用标签灰色，引用正文白色；链接仍在提示框首行。"""
    heading, separator, body = reference.partition("\n")
    return {"text": link + "\n\n" if link else "", "color": "white", "extra": [
        {"text": heading, "color": "gray"},
        {"text": separator + body, "color": "white"},
    ]}


def http_url(value: object) -> str:
    """过滤非网页协议，避免消息内容成为可执行点击动作。"""
    if not isinstance(value, str) or any(char.isspace() for char in value):
        return ""
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in {"http", "https"} and parsed.netloc else ""
    except ValueError:
        return ""


def component_kind(component: object) -> str:
    """兼容 AstrBot 枚举及测试桩的组件类型。"""
    kind = getattr(component, "type", "")
    return str(getattr(kind, "value", kind) or type(component).__name__)


def source_image_urls(raw_message: object) -> list[str]:
    """读取原始 OneBot 图片段，保留缺失项的位置供组件一一对应。"""
    if not isinstance(raw_message, dict):
        return []
    chain = raw_message.get("message", [])
    if not isinstance(chain, list):
        return []
    result = []
    for part in chain:
        if isinstance(part, dict) and part.get("type") == "image":
            data = part.get("data", {})
            result.append((http_url(data.get("url")) or http_url(data.get("file"))) if isinstance(data, dict) else "")
    return result


async def event_reply_context(event: object) -> str:
    """获取悬停引用正文；图片用标记展示，避免 CDN 长链接撑满提示框。"""
    return reply_context(event.get_messages() or [])


def reply_author(components: list) -> str:
    """获取被引用消息的 QQ 作者，游戏玩家名由调用方优先解析。"""
    reply = next((part for part in components if component_kind(part) == "Reply"), None)
    if reply is None:
        return ""
    author = getattr(reply, "sender_nickname", "") or getattr(reply, "sender_id", "") or "原消息"
    return " ".join(str(author).split())


def card_links(components: list) -> list[str]:
    """提取 QQ 分享卡片的目标网页，不把预览图当成文章链接。"""
    result: list[str] = []

    def visit(value: object, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in {"url", "qqdocurl", "jumpurl", "jump_url", "weburl", "web_url"}:
                    link = http_url(child)
                    if link and link not in result:
                        result.append(link)
                elif isinstance(child, (dict, list)):
                    visit(child, depth + 1)
        elif isinstance(value, list):
            for child in value[:30]:
                visit(child, depth + 1)

    for component in components:
        if component_kind(component) == "Json":
            visit(getattr(component, "data", {}))
        elif component_kind(component) == "Share":
            link = http_url(getattr(component, "url", ""))
            if link and link not in result:
                result.append(link)
    return result


def reply_context(components: list) -> str:
    """保留被引用正文，图片仅显示标记，避免提示框出现大段 CDN 地址。"""
    reply = next((part for part in components if component_kind(part) == "Reply"), None)
    if reply is None:
        return ""
    body = str(getattr(reply, "message_str", "") or getattr(reply, "text", "") or "")
    chain = getattr(reply, "chain", None) or []
    if not body:
        body = "".join(str(getattr(part, "text", "")) for part in chain if component_kind(part) == "Plain")
    links = card_links(chain)
    for part in chain:
        if component_kind(part) == "Image":
            link = http_url(getattr(part, "url", "")) or http_url(getattr(part, "file", ""))
            if link and link in body:
                body = body.replace(link, "[图片]")
            if "[图片]" not in body:
                body += " [图片]"
    for link in dict.fromkeys(links):
        if link not in body:
            body += " " + link
    if not body.strip():
        body = f"消息 #{getattr(reply, 'id', '')}（原文未取得）"
    return "回复内容：\n" + body.strip()[:2000]


def interactive(text: str, action: str, value: str, hover: str) -> dict:
    """兼容旧版和 1.21.5+ 的文本事件字段；只生成链接或输入框填充动作。"""
    field = "url" if action == "open_url" else "command"
    return {"text": text, "color": "aqua", "underlined": True,
            "clickEvent": {"action": action, "value": value},
            "click_event": {"action": action, field: value},
            "hoverEvent": {"action": "show_text", "contents": {"text": hover}},
            "hover_event": {"action": "show_text", "value": {"text": hover}}}


def rich_components(text: str | list[dict], color: str, image_links: list[str], reference: str = "") -> list[dict] | None:
    """生成链接及引用组件；普通文本返回 None，保留原来的广播路径。"""
    parts: list[dict] = []
    has_links = False
    styled = isinstance(text, list)
    for base in text if styled else [{"text": text, "color": color}]:
        value = base["text"]
        position = 0
        for match in URL_PATTERN.finditer(value):
            # ChatImage 代码自身必须保留为完整纯文本。
            if value.rfind("[[CICode,", 0, match.start()) > value.rfind("]]", 0, match.start()):
                continue
            url = match.group().rstrip(".,;!?，。；！？）)")
            if not http_url(url):
                continue
            if match.start() > position:
                parts.append({**base, "text": value[position:match.start()]})
            link = {**base, **interactive("[链接]", "open_url", url, url)}
            if styled:
                link["color"] = base["color"]
            parts.append(link)
            position = match.start() + len(url)
            has_links = True
        if position < len(value):
            parts.append({**base, "text": value[position:]})
    for index, url in enumerate(image_links, 1):
        if http_url(url):
            parts.append(interactive(f" [图片{index}]", "open_url", url, "点击查看图片\n" + url))
    if reference:
        for part in parts:
            link = part.get("clickEvent", {}).get("value", "")
            hover = quote_hover(reference, link)
            part["hoverEvent"] = {"action": "show_text", "contents": hover}
            part["hover_event"] = {"action": "show_text", "value": hover}
    return parts if styled or has_links or image_links or reference else None
