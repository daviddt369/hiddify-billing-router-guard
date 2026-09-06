from __future__ import annotations

import ast
import hmac
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
PAYLOAD = (
    ROOT
    / "release"
    / "business-installer"
    / "payload"
    / "panel-overlay"
    / "hiddifypanel"
    / "panel"
    / "commercial"
    / "restapi"
)
ROUTE_MODULE = PAYLOAD / "v2" / "telegram" / "__init__.py"
BOT_MODULES = (
    PAYLOAD / "v1" / "tgbot.py",
    PAYLOAD / "v2" / "telegram" / "tgbot.py",
)


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def find_function(path: Path, name: str) -> ast.FunctionDef:
    matches = [
        node
        for node in parse(path).body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one {name} in {path}, found {len(matches)}")
    return matches[0]


def load_secret_validator(path: Path):
    node = find_function(path, "_webhook_secret_is_valid")
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "_webhook_secret": lambda: "expected-secret",
        "hmac": hmac,
        "logger": mock.Mock(),
    }
    exec(compile(module, str(path), "exec"), namespace)
    return namespace["_webhook_secret_is_valid"]


class TelegramWebhookRouteTest(unittest.TestCase):
    def test_route_omits_uuid_and_registers_tgbot_resource(self):
        tree = parse(ROUTE_MODULE)
        prefixes = []
        resource_paths = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "APIBlueprint":
                    for keyword in node.keywords:
                        if keyword.arg == "url_prefix" and isinstance(
                            keyword.value, ast.Constant
                        ):
                            prefixes.append(keyword.value.value)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_resource"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
            ):
                resource_paths.append(node.args[1].value)

        self.assertEqual(prefixes, ["/<proxy_path>/api/v2/"])
        self.assertNotIn("uuid", prefixes[0].lower())
        self.assertIn("tgbot/", resource_paths)

    def test_registered_webhook_url_matches_uuid_free_route(self):
        for path in BOT_MODULES:
            with self.subTest(module=path.name, parent=path.parent.name):
                register = find_function(path, "register_bot")
                urls = [
                    node
                    for node in ast.walk(register)
                    if isinstance(node, ast.JoinedStr)
                    and "/api/v2/tgbot/"
                    in "".join(
                        value.value
                        for value in node.values
                        if isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                    )
                ]
                self.assertEqual(len(urls), 1)
                rendered_ast = ast.unparse(urls[0]).lower()
                self.assertNotIn("uuid", rendered_ast)
                self.assertNotIn("secret_uuid", rendered_ast)

    def test_secret_header_is_validated_directly(self):
        for path in BOT_MODULES:
            with self.subTest(module=path.name, parent=path.parent.name):
                validate = load_secret_validator(path)
                request = types.SimpleNamespace(
                    headers={}, path="/admin/api/v2/tgbot/", remote_addr="127.0.0.1"
                )
                self.assertFalse(validate(request))

                request.headers["X-Telegram-Bot-Api-Secret-Token"] = "wrong"
                self.assertFalse(validate(request))

                request.headers["X-Telegram-Bot-Api-Secret-Token"] = (
                    "expected-secret"
                )
                self.assertTrue(validate(request))

    def test_resource_never_constructs_a_500_response(self):
        for path in BOT_MODULES:
            with self.subTest(module=path.name, parent=path.parent.name):
                resource = next(
                    node
                    for node in parse(path).body
                    if isinstance(node, ast.ClassDef) and node.name == "TGBotResource"
                )
                statuses = set()
                for node in ast.walk(resource):
                    if not (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "Response"
                    ):
                        continue
                    for keyword in node.keywords:
                        if (
                            keyword.arg == "status"
                            and isinstance(keyword.value, ast.Constant)
                            and isinstance(keyword.value.value, int)
                        ):
                            statuses.add(keyword.value.value)
                    if (
                        len(node.args) >= 2
                        and isinstance(node.args[1], ast.Constant)
                        and isinstance(node.args[1].value, int)
                    ):
                        statuses.add(node.args[1].value)
                self.assertNotIn(500, statuses)
                self.assertTrue({200, 403, 503}.issubset(statuses))


if __name__ == "__main__":
    unittest.main()
