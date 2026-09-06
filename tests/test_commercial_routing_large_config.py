from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


SOURCE = (
    Path(__file__).parents[1]
    / "release"
    / "routing-installer"
    / "payload"
    / "panel-overlay"
    / "hiddifypanel"
    / "hutils"
    / "commercial_routing.py"
)


class RouterApplyResult:
    def __init__(self, **values):
        self.__dict__.update(values)


def load_primary_apply_function():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    candidates = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "apply_router_core_config"
    ]
    if not candidates:
        raise AssertionError("primary apply_router_core_config not found")
    primary = min(candidates, key=lambda node: node.lineno)
    module = ast.Module(body=[primary], type_ignores=[])
    ast.fix_missing_locations(module)

    written_paths = []

    def write_router_json(path, config):
        written_paths.append(path)
        path.write_bytes(config)

    namespace = {
        "RouterApplyResult": RouterApplyResult,
        "load_enabled_custom_rules": lambda: [],
        "_router_apply_xray_binary": lambda: "xray",
        "_write_router_json": write_router_json,
    }
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace["apply_router_core_config"], written_paths


class CommercialRoutingLargeConfigTest(unittest.TestCase):
    def setUp(self):
        self.apply, self.written_paths = load_primary_apply_function()

    def _modules(self, target: Path, config: bytes):
        rendered = types.SimpleNamespace(
            target_path=str(target),
            config=config,
            service_name="xray-router",
        )
        router_core = types.SimpleNamespace(
            render_desired_config=lambda *_args, **_kwargs: rendered
        )

        hiddifypanel = types.ModuleType("hiddifypanel")
        models = types.ModuleType("hiddifypanel.models")
        models.get_hconfigs = lambda: {}
        hutils = types.ModuleType("hiddifypanel.hutils")
        proxy = types.ModuleType("hiddifypanel.hutils.proxy")
        proxy.router_core = router_core

        return {
            "hiddifypanel": hiddifypanel,
            "hiddifypanel.models": models,
            "hiddifypanel.hutils": hutils,
            "hiddifypanel.hutils.proxy": proxy,
        }

    @staticmethod
    def _run_result(command, **_kwargs):
        if command[:2] == ["systemctl", "is-active"]:
            return types.SimpleNamespace(returncode=0, stdout="active\n", stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    def test_large_valid_json_skips_xray_test(self):
        config = json.dumps({"padding": "x" * 600_000}).encode()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config.json"
            with mock.patch.dict(sys.modules, self._modules(target, config), clear=False):
                with mock.patch("subprocess.run", side_effect=self._run_result) as run:
                    result = self.apply()

            commands = [call.args[0] for call in run.call_args_list]
            self.assertFalse(any("-test" in command for command in commands))
            self.assertEqual(target.read_bytes(), config)
            self.assertEqual(result.target_path, str(target))

    def test_small_json_uses_xray_test_with_120_second_timeout(self):
        config = b'{"routing": {"rules": []}}'
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config.json"
            with mock.patch.dict(sys.modules, self._modules(target, config), clear=False):
                with mock.patch("subprocess.run", side_effect=self._run_result) as run:
                    self.apply()

            test_calls = [
                call for call in run.call_args_list if "-test" in call.args[0]
            ]
            self.assertEqual(len(test_calls), 1)
            self.assertEqual(test_calls[0].kwargs["timeout"], 120)

    def test_large_invalid_json_is_rejected_and_temp_file_removed(self):
        config = b"{" + (b"x" * 600_000)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config.json"
            with mock.patch.dict(sys.modules, self._modules(target, config), clear=False):
                with mock.patch("subprocess.run", side_effect=self._run_result) as run:
                    with self.assertRaisesRegex(RuntimeError, "xray config JSON parse error"):
                        self.apply()

            run.assert_not_called()
            self.assertEqual(len(self.written_paths), 1)
            self.assertFalse(self.written_paths[0].exists())
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
