#!/usr/bin/env python3
"""Default texts of the Telegram bot for a NEW node (cabinet/bot/): the instruction, the welcome message and the link / button texts.
Only what is still empty is filled: a text the owner has written (or edited in the panel) is never touched.

usage: seed_bot_texts.py [--panel-python PATH]   (run on the node, as root, after the panel is installed)
       seed_bot_texts.py --render FILE           (print FILE with the guide-page link resolved for this node; changes nothing)"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BOT = os.path.join(HERE, "..", "cabinet", "bot")
UI = "/opt/hiddify-manager/hiddify-panel/var/business-telegram-ui.json"
CABINET_JSON = "/opt/hiddify-manager/cabinet/cabinet.json"
GUIDE_RE = re.compile(r"^https://[A-Za-z0-9.\-]+(/[A-Za-z0-9._~%\-/]*)?(#[A-Za-z0-9_\-]+)?$")
PANEL_DIR = "/opt/hiddify-manager/hiddify-panel"
ap = argparse.ArgumentParser()
ap.add_argument("--panel-python", default="/opt/hiddify-manager/.venv313/bin/python")
ap.add_argument("--render", metavar="FILE")
a = ap.parse_args()


def current(key: str) -> str:
    r = subprocess.run(["mysql", "--default-character-set=utf8mb4", "--raw", "-N", "-B", "hiddifypanel", "-e",
                        f"select value from str_config where `key`='{key}'"], capture_output=True, text=True)
    return r.stdout.strip()


def read(name: str) -> str:
    with open(os.path.join(BOT, name), encoding="utf-8") as f:
        return f.read().strip()


def guide_url() -> str:
    """The page with the full "change the App Store region" instruction (cabinet.json, guide_url); empty when this node has no such page."""
    try:
        with open(CABINET_JSON, encoding="utf-8") as f:
            v = str(json.load(f).get("guide_url") or "")
    except (OSError, ValueError, AttributeError):
        return ""
    return v if GUIDE_RE.match(v) else ""


def render(text: str) -> str:
    """[[GUIDE]]...[[/GUIDE]] (with @@GUIDE_URL@@ inside) stays only if the node has the guide page; the markers never reach the bot."""
    g = guide_url()
    if g:
        return text.replace("[[GUIDE]]", "").replace("[[/GUIDE]]", "").replace("@@GUIDE_URL@@", g)
    return re.sub(r"\[\[GUIDE\]\].*?\[\[/GUIDE\]\]", "", text, flags=re.S)


if a.render:
    with open(a.render, encoding="utf-8") as f:
        print(render(f.read().strip()))
    sys.exit(0)

done = []
for key, fname in (("telegram_instruction_message", "instruction.html"), ("telegram_welcome_message", "welcome.txt")):
    if current(key):
        continue
    r = subprocess.run([a.panel_python, "-m", "hiddifypanel", "set-setting", "-k", key, "-v", render(read(fname))], cwd=PANEL_DIR, capture_output=True, text=True)
    done.append(f"{key}: {'set' if r.returncode == 0 else 'FAILED'}")

# link / button texts of the bot (business-telegram-ui.json, key bot_texts): only the slugs that are not there yet
try:
    with open(UI, encoding="utf-8") as f:
        ui = json.load(f)
except (OSError, ValueError):
    ui = {}
texts = ui.setdefault("bot_texts", {})
new = json.loads(read("bot_texts.json"))
added = [k for k, v in new.items() if not str(texts.get(k, "")).strip()]
for k in added:
    texts[k] = new[k]
if added or not os.path.exists(UI):
    os.makedirs(os.path.dirname(UI), exist_ok=True)
    existed = os.path.exists(UI)
    with open(UI, "w", encoding="utf-8") as f:
        f.write(json.dumps(ui, ensure_ascii=False, indent=2) + "\n")
    if not existed:  # the panel itself rewrites this file when the page "Бизнес → Telegram-бот" is saved: same owner as its folder
        st = os.stat(os.path.dirname(UI))
        os.chown(UI, st.st_uid, st.st_gid)
    done.append("bot_texts: " + (", ".join(added) if added else "created"))
print("seed_bot_texts:", "; ".join(done) if done else "nothing to do (the texts are already there)")
sys.exit(0)
