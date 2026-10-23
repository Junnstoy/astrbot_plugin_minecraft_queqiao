"""Offline regression tests for the actual rich-message conversion functions."""
import ast
import importlib.util
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rich_message_under_test", ROOT / "services/rich_message.py")
rich = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rich)

# Compile production helpers without importing the AstrBot runtime or mocks of
# the converter itself. The same function is used by MC-to-QQ forwarding.
bridge_path = ROOT / "services/message_bridge.py"
tree = ast.parse(bridge_path.read_text(encoding="utf-8"))
helpers = [node for node in tree.body if isinstance(node, ast.FunctionDef)
           and node.name in {"strip_formatting", "component_to_text"}]
namespace = {"json": json, "http_url": rich.http_url, "URL_PATTERN": rich.URL_PATTERN,
             "_FORMAT_CODE_RE": re.compile(r"§[0-9a-fk-orA-FK-OR]")}
exec(compile(ast.Module(body=helpers, type_ignores=[]), str(bridge_path), "exec"), namespace)
decode = namespace["component_to_text"]


class RichMessageTests(unittest.TestCase):
    def test_links_roundtrip_with_punctuation_and_modern_fields(self):
        url = "https://example.com/page?a=1&b=2"
        parts = rich.rich_components("看看 " + url + "。", "white", [])
        link = next(part for part in parts if part["text"] == "[链接]")
        self.assertEqual(link["clickEvent"]["value"], url)
        self.assertEqual(link["click_event"]["url"], url)
        self.assertEqual(link["hover_event"]["value"]["text"], url)
        self.assertEqual(decode(json.dumps(parts)), "看看 " + url + "。")

    def test_link_restoration_from_inherited_hover_and_legacy_click(self):
        url = "https://example.com/original"
        cases = [
            {"text": "[链接]", "clickEvent": {"action": "open_url", "value": url}},
            {"text": "", "hover_event": {"action": "show_text", "value": {"text": url}},
             "extra": [{"text": "[链接]"}]},
        ]
        for component in cases:
            with self.subTest(component=component):
                self.assertEqual(decode(json.dumps(component)), url)

    def test_non_http_or_command_click_is_not_used_as_a_link(self):
        for action, value in (("run_command", "/op Someone"), ("open_url", "javascript:alert(1)")):
            component = {"text": "[链接]", "clickEvent": {"action": action, "value": value}}
            self.assertEqual(decode(json.dumps(component)), "[链接]")

    def test_explicit_reply_inherits_template_style_without_parsing_body(self):
        body = "正文 &c {reply}"
        parts = rich.format_broadcast("&e{sender}&a&l{reply}&r：{message}",
                                      {"sender": "Alice", "message": body}, "white", "Bob")
        reply = next(part for part in parts if part["text"] == "(回复 @Bob)")
        self.assertEqual(reply["color"], "green")
        self.assertTrue(reply["bold"])
        message = next(part for part in parts if part["text"] == body)
        self.assertEqual(message["color"], "white")
        self.assertFalse(message["bold"])

    def test_legacy_reply_is_gray_and_hover_keeps_quote_body(self):
        parts = rich.format_broadcast("&e{player}：{message}",
                                      {"player": "Alice", "message": "收到"}, "white", "Bob")
        reply = next(part for part in parts if part["text"] == "(回复 @Bob)")
        self.assertEqual(reply["color"], "gray")
        result = rich.rich_components(parts, "white", [], "回复内容：\n原消息")
        hover = result[0]["hoverEvent"]["contents"]
        self.assertEqual(hover["extra"][0], {"text": "回复内容：", "color": "gray"})
        self.assertEqual(hover["extra"][1], {"text": "\n原消息", "color": "white"})
        self.assertTrue(all("clickEvent" not in part for part in result))

    def test_images_are_clickable_and_chatimage_code_is_preserved(self):
        url = "https://example.com/image.png"
        code = "[[CICode,url=" + url + ",name=图片]]"
        self.assertIsNone(rich.rich_components(code, "white", []))
        image = rich.rich_components("", "white", [url])[0]
        self.assertIn("图片1", image["text"])
        self.assertEqual(image["click_event"]["url"], url)

    def test_source_urls_preserve_missing_positions(self):
        raw = {"message": [
            {"type": "text", "data": {"text": "图片"}},
            {"type": "image", "data": {"url": "https://example.com/1.png"}},
            {"type": "image", "data": {"file": "local.png"}},
            {"type": "image", "data": {"file": "https://example.com/3.png"}},
        ]}
        self.assertEqual(rich.source_image_urls(raw), ["https://example.com/1.png", "", "https://example.com/3.png"])


if __name__ == "__main__":
    unittest.main()
