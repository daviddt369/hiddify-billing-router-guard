"""builds cp18/BusinessAdmin.py from the live one: the bot-text editor of the Telegram page (validation before anything is saved, save with the rest, groups for the template).
usage: make_business.py <BusinessAdmin.py in> <out>"""
import sys

src = open(sys.argv[1], encoding="utf-8").read()
pairs = [
    # the page gets the editable texts
    ("            show_section_tabs=False,\n        )\n",
     "            show_section_tabs=False,\n            bot_text_groups=_tg_text_groups() if active_section == \"telegram\" else [],\n            bot_trial=_tg_trial() if active_section == \"telegram\" else {},\n        )\n"),
    # validate the texts before anything is saved
    ("        if (form.commercial_router_port.data or \"\").strip():\n            try:\n                port = int(",
     "        if self._active_section(old_configs) == \"telegram\":\n            try:\n                from hiddifypanel.hutils import tg_texts\n                tg_texts.check_form(request.form)\n            except ValueError as exc:\n                hutils.flask.flash(str(exc), \"danger\")\n                return self._render_settings(form, commercial_routing_notice=None, test_result=None)\n\n        if (form.commercial_router_port.data or \"\").strip():\n            try:\n                port = int("),
    # save them together with the other telegram settings
    ("        _write_telegram_ui_value(\"telegram_registration_mode\", telegram_registration_mode)\n",
     "        _write_telegram_ui_value(\"telegram_registration_mode\", telegram_registration_mode)\n        if self._active_section(old_configs) == \"telegram\":\n            from hiddifypanel.hutils import tg_texts\n            tg_texts.save_from_form(request.form)\n"),
    # helper
    ("def _normalize_multiline_text(value: str) -> str:\n",
     "def _tg_text_groups() -> list:\n    try:\n        from hiddifypanel.hutils import tg_texts\n        return tg_texts.form_groups()\n    except Exception:\n        return []\n\n\ndef _tg_trial() -> dict:\n    try:\n        from hiddifypanel.hutils import tg_texts\n        return tg_texts.trial_form()\n    except Exception:\n        return {}\n\n\ndef _normalize_multiline_text(value: str) -> str:\n"),
]
for old, new in pairs:
    assert src.count(old) == 1, old[:70]
    src = src.replace(old, new, 1)
open(sys.argv[2], "w", encoding="utf-8", newline="\n").write(src)
print("ok")
