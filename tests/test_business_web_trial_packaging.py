from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
INSTALLER = ROOT / "release" / "business-installer" / "install-business.sh"
SMOKE = ROOT / "release" / "business-installer" / "smoke-business.sh"
COMMON = ROOT / "release" / "business-installer" / "common.sh"

WEB_TRIAL_FILES = (
    "panel/commercial/restapi/v2/web_trial/__init__.py",
    "panel/commercial/restapi/v2/web_trial/trial.py",
)


class BusinessWebTrialPackagingTest(unittest.TestCase):
    def test_runtime_files_are_installed_and_compiled(self):
        source = INSTALLER.read_text(encoding="utf-8")
        for suffix in WEB_TRIAL_FILES:
            payload = f'panel-overlay/hiddifypanel/{suffix}'
            target = f'$panel_root/{suffix}'
            install_pattern = re.compile(
                rf'install_payload_file "{re.escape(payload)}" "{re.escape(target)}" 0644'
            )
            self.assertRegex(source, install_pattern)
            self.assertGreaterEqual(source.count(f'"{target}"'), 2)

    def test_installer_import_smoke_covers_web_trial(self):
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertIn(
            "hiddifypanel.panel.commercial.restapi.v2.web_trial.trial", source
        )

    def test_standalone_smoke_covers_web_trial(self):
        source = SMOKE.read_text(encoding="utf-8")
        self.assertIn(
            "hiddifypanel.panel.commercial.restapi.v2.web_trial.trial", source
        )

    def test_site_assets_are_not_installed_into_panel_runtime(self):
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertNotIn("widget.html", source)
        self.assertNotIn("wordpress-proxy.php", source)

    def test_new_runtime_files_are_recorded_and_removed_on_rollback(self):
        source = COMMON.read_text(encoding="utf-8")
        backup = re.search(
            r"backup_target\(\) \{(?P<body>.*?)\n\}", source, re.DOTALL
        )
        rollback = re.search(
            r"rollback_backup_dir\(\) \{(?P<body>.*?)\n\}", source, re.DOTALL
        )
        self.assertIsNotNone(backup)
        self.assertIsNotNone(rollback)
        self.assertIn('>> "$BACKUP_DIR/created-files.txt"', backup.group("body"))
        self.assertIn(
            'tac "$backup_dir/created-files.txt"', rollback.group("body")
        )
        self.assertIn('rm -f "$created_file"', rollback.group("body"))


if __name__ == "__main__":
    unittest.main()
