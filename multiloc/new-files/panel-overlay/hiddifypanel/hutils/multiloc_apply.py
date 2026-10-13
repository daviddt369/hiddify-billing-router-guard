"""ONE "Apply" for the whole routing area - panel side. The runner is scripts/multiloc_router_apply.py (root, a systemd service); the panel starts it through its
existing sudo entry point `commander.py router-apply-start` and never waits for it.

The owner's rule: press "Apply" and everything is done; restart the router only when it is really needed. So the button decides by itself:
  * nothing changed since the last apply                      -> nothing is done, no restart
  * only the countries layer differs from the database        -> `countries`: ONE router restart (production: ~4-8 min without connections)
  * the rules / external nodes / routing settings changed     -> `full`: the legacy pipeline built in the background (router untouched for ~15-20 min on production), then ONE restart
"Changed" is exact, not guessed: legacy_parts() hashes every input the legacy pipeline reads (the enabled custom rules, the active external nodes without their
volatile probe fields, the commercial_* settings, the routing UI json files); the root run records those hashes after a successful full apply
(/var/lib/multiloc-router-apply/applied.json). The countries layer has its own exact check (multiloc_router.pending()).
Installed by the multiloc installer.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time

COMMANDER = "/opt/hiddify-manager/common/commander.py"
STATE_DIR = "/var/lib/multiloc-router-apply"
STATE_FILE = STATE_DIR + "/status.json"
APPLIED_FILE = STATE_DIR + "/applied.json"
LOG_FILE = "/var/log/multiloc-router-apply.log"
UNIT = "multiloc-router-apply"
MODES = ("auto", "countries", "full", "accept", "discard")
LIVE_STATES = ("active", "activating", "deactivating", "reloading")

TITLE = {"auto": "«Применить»", "countries": "только страны (быстрый путь)", "full": "полный цикл (правила, ноды, страны)",
         "accept": "применение собранного конфига (после вашего подтверждения)", "discard": "отказ от собранного конфига"}
STATE_TEXT = {"idle": "не запускалось", "running": "идёт", "ok": "завершено успешно", "failed": "завершилось с ошибкой", "aborted": "прервано",
              "needs-confirm": "ждёт вашего решения"}
STAGED_META = STATE_DIR + "/staged/meta.json"             # the finished router config that waits for the owner's decision (written by hutils/multiloc_full.py)
LIVE_CONFIG = "/etc/xray-router/config.json"
_LAST: dict = {"parts": None, "meta": {}}               # what the last legacy_parts() saw (reused by plan() / bar() / record_applied())
PART_TEXT = {"rules": "правила маршрутизации", "upstreams": "внешние ноды", "settings": "настройки маршрутизации", "ui_files": "списки блокировки и DNS"}
_UPSTREAM_VOLATILE = {"last_status", "last_error", "last_checked_at", "created_at", "updated_at"}     # probe results: they say nothing about the router config
_PLAN_CACHE: dict = {"at": 0.0, "plan": None}
PLAN_TTL = 15.0


# ----------------------------------------------------------------------------------------------- what the legacy pipeline reads
def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def legacy_parts() -> dict[str, str]:
    """{part: sha256} of every input of the legacy router pipeline. Needs an application context (database)."""
    from hiddifypanel.hutils import commercial_routing as cr
    from hiddifypanel.models import get_hconfigs
    # the rules WITHOUT their ids: the routing "Save" deletes and re-creates every rule (new ids, same content and order) and that must not look like a change
    rules_now = cr.load_enabled_custom_rules()
    meta = {"rules": len(rules_now)}
    parts = {"rules": _h([{k: v for k, v in r.items() if k != "id"} for r in rules_now])}
    ups = []
    try:
        from hiddifypanel.models.commercial_routing_upstream import CommercialRoutingUpstream
        for u in CommercialRoutingUpstream.get_active_ordered() or []:
            ups.append({c.name: getattr(u, c.name) for c in u.__table__.columns if c.name not in _UPSTREAM_VOLATILE})
        meta["upstreams"] = len(ups)
    except Exception:
        ups = None
    parts["upstreams"] = _h(ups)
    parts["settings"] = _h({str(k): v for k, v in (get_hconfigs() or {}).items() if str(k).startswith("commercial")})
    files = {}
    for path in cr.commercial_routing_ui_read_paths():
        try:
            with open(path, "rb") as f:
                files[path] = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            files[path] = None
    parts["ui_files"] = _h(files)
    _LAST["parts"], _LAST["meta"] = parts, meta
    return parts


def legacy_meta() -> dict:
    """the counts of the inputs seen by the last legacy_parts() (shown before a full cycle: 'rules: 15076 -> 2073'); not part of the comparison"""
    return dict(_LAST["meta"])


def applied() -> dict | None:
    try:
        with open(APPLIED_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("legacy"), dict) else None
    except (OSError, ValueError):
        return None


def record_applied(parts: dict, how: str, meta: dict | None = None) -> None:
    """ROOT only (the runner / multiloc_ctl): remember which inputs the router now reflects (and their counts, for the text before the next full cycle)"""
    os.makedirs(STATE_DIR, exist_ok=True)
    os.chmod(STATE_DIR, 0o755)
    tmp = APPLIED_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"legacy": parts, "at": time.time(), "how": how, "meta": meta if meta is not None else legacy_meta()}, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, APPLIED_FILE)


def staged_info(parts_now: dict | None = None) -> dict | None:
    """the finished router config that waits for the owner's decision - with `valid`: the running router config and the inputs are still what it was built from"""
    try:
        with open(STAGED_META, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    try:
        import hashlib
        with open(LIVE_CONFIG, "rb") as f:
            valid = hashlib.sha256(f.read()).hexdigest() == meta.get("live_sha")
    except OSError:
        valid = False
    if valid and parts_now is not None and meta.get("parts") not in (None, parts_now):
        valid = False
    return {"valid": valid, "built_text": meta.get("built_text"), "unexplained": meta.get("unexplained", []), "explained": meta.get("explained", []),
            "warnings": meta.get("warnings", []), "expected": meta.get("expected", [])}


def plan(use_cache: bool = False) -> dict:
    """What "Apply" would do right now. {"action": none|countries|full, "restarts": int, "changed": [parts], "text": ..., "confirm": ...}. Needs an application context."""
    if use_cache and _PLAN_CACHE["plan"] is not None and time.monotonic() - _PLAN_CACHE["at"] < PLAN_TTL:
        return _PLAN_CACHE["plan"]
    now = legacy_parts()
    ap = applied()
    changed = [] if ap is None else [k for k in now if now[k] != ap["legacy"].get(k)]
    legacy = "unknown" if ap is None else ("pending" if changed else "clean")
    staged = staged_info(now)
    countries, missing = "unknown", {}
    try:
        from hiddifypanel.hutils import multiloc, multiloc_router
        if multiloc.schema_present():
            p = multiloc_router.pending()
            missing = p.get("missing_nodes") or {}
            countries = "pending" if p.get("in_sync") is False else ("clean" if p.get("in_sync") is True else "unknown")
        else:
            countries = "clean"
    except Exception:
        pass
    if staged and staged["valid"]:
        out = {"action": "confirm", "restarts": 1, "legacy": legacy, "countries": countries, "changed": changed, "confirm": "", "checked_at": time.time(),
               "text": "Новый конфиг router собран, но он отличается от рабочего не только вашими правками - он ждёт вашего решения (ниже). Router не тронут."}
        _PLAN_CACHE.update(at=time.monotonic(), plan=out)
        return out
    if legacy in ("pending", "unknown") or missing:
        action = "full"
    elif countries in ("pending", "unknown"):
        action = "countries"
    else:
        action = "none"
    nowm, was = legacy_meta(), ((ap or {}).get("meta") or {})
    texts = {k: PART_TEXT[k] for k in changed}
    if "rules" in texts and "rules" in was and "rules" in nowm and was["rules"] != nowm["rules"]:
        texts["rules"] += f" (было {was['rules']}, станет {nowm['rules']})"
    if "upstreams" in texts and "upstreams" in was and "upstreams" in nowm and was["upstreams"] != nowm["upstreams"]:
        texts["upstreams"] += f" (было {was['upstreams']}, станет {nowm['upstreams']})"
    what = ", ".join(texts[k] for k in changed) if changed else ("состояние router неизвестно (применение ещё не отслеживалось)" if legacy == "unknown" else "")
    if missing:
        what = (what + "; " if what else "") + "ноды стран, которых нет в конфиге router"
    if action == "full":
        text = (f"Изменено: {what}. Нужен полный цикл: новый конфиг готовится в фоне (на проде около 15–20 минут, router всё это время работает как раньше). "
                "Если он отличается от рабочего только вашими правками - ОДИН перезапуск router (на проде ≈ 4–8 минут без соединений через РФ-ноду); "
                "если есть другие отличия - конфиг будет собран и покажет, чем отличается, а router не тронется, пока вы не подтвердите.")
        confirm = ("Применить?\n\n" + text + "\n\nИдёт в фоне — страницу можно закрыть, ход виден здесь. Повторно кнопку не нажимайте.")
        restarts = 1
    elif action == "countries":
        text = "Изменены страны (ноды или включение). Нужен один перезапуск router: на проде ≈ 4–8 минут без соединений через РФ-ноду."
        confirm = "Применить?\n\n" + text + "\n\nИдёт в фоне — страницу можно закрыть, ход виден здесь."
        restarts = 1
    else:
        text = "Всё применено. Перезапуск router не нужен."
        confirm = ""
        restarts = 0
    out = {"action": action, "restarts": restarts, "legacy": legacy, "countries": countries, "changed": changed, "text": text, "confirm": confirm, "checked_at": time.time()}
    _PLAN_CACHE.update(at=time.monotonic(), plan=out)
    return out


# ----------------------------------------------------------------------------------------------- start / status
def start(mode: str = "auto") -> dict:
    """Ask root to start the background apply. Returns the runner's JSON answer: {"started": True, ...} or {"started": False, "reason": ..., "detail": ...}.
    Never raises, never waits for the apply itself (the runner answers within seconds)."""
    if mode not in MODES:
        return {"started": False, "reason": "bad-mode", "detail": f"mode {mode!r}"}
    _PLAN_CACHE["plan"] = None
    try:
        r = subprocess.run(["sudo", "-n", COMMANDER, "router-apply-start", "--mode", mode], capture_output=True, text=True, timeout=45)
    except Exception as exc:
        return {"started": False, "reason": "error", "detail": str(exc)[:300]}
    lines = [ln for ln in (r.stdout or "").strip().splitlines() if ln.strip().startswith("{")]
    if lines:
        try:
            res = json.loads(lines[-1])
            if isinstance(res, dict) and "started" in res:
                return res
        except ValueError:
            pass
    detail = ((r.stderr or "") + " " + (r.stdout or "")).strip()
    return {"started": False, "reason": "no-answer", "detail": detail[-300:] or f"rc={r.returncode}"}


def apply_now() -> tuple[dict, dict]:
    """The one action behind every "Apply" button: decide, start if there is something to do. Returns (plan, start-result)."""
    pl = plan()
    if pl["action"] == "none":
        return pl, {"started": False, "reason": "nothing", "detail": "nothing to apply"}
    return pl, start("auto")


def _unit_live() -> bool:
    try:
        r = subprocess.run(["systemctl", "show", UNIT, "-p", "ActiveState", "--value"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() in LIVE_STATES
    except Exception:
        return False


def _tail(path: str, n: int) -> list[str]:
    if n <= 0:
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 32768))
            return f.read().decode("utf-8", "replace").splitlines()[-n:]
    except OSError:
        return []


def status(tail: int = 25) -> dict:
    """What the page shows: state (idle | running | ok | failed | aborted), mode, texts, seconds, the end of the log. Reads files only (plus one systemctl query)."""
    st: dict = {"state": "idle"}
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            st = data
    except (OSError, ValueError):
        pass
    live = _unit_live()
    if st.get("state") == "running" and not live:
        st = dict(st, state="aborted", summary=st.get("summary") or "процесс исчез (остановлен или сервер перезагружался)")
    st["unit_live"] = live
    st["state_text"] = STATE_TEXT.get(st.get("state"), str(st.get("state")))
    st["mode_text"] = TITLE.get(st.get("mode"), st.get("mode") or "")
    started, finished = st.get("started"), st.get("finished")
    if isinstance(started, (int, float)):
        st["elapsed"] = int((finished if (st.get("state") != "running" and isinstance(finished, (int, float))) else time.time()) - started)
    st["log_tail"] = _tail(LOG_FILE, tail)
    return st


def bar(use_cache: bool = False) -> dict:
    """everything the "Apply" bar of a page needs (status + plan); never raises. Never cached by default: the plan is a few hundred ms and must be current"""
    try:
        st = status()
    except Exception as exc:
        st = {"state": "idle", "state_text": "состояние неизвестно: " + str(exc)[:80], "log_tail": []}
    try:
        pl = plan(use_cache=use_cache)
    except Exception as exc:
        pl = {"action": "unknown", "text": "Не удалось определить, что нужно применить: " + str(exc)[:120], "confirm": "Применить?", "changed": [], "restarts": 0}
    staged = None
    try:
        s = staged_info(_LAST["parts"])
        staged = s if s and s["valid"] else None
    except Exception:
        staged = None
    return {"status": st, "plan": pl, "staged": staged}


def flash_for_decision(result: dict, mode: str) -> tuple[str, str]:
    """(text, category) after «Применить новый конфиг» (accept) or «Не применять» (discard)"""
    if result.get("started"):
        if mode == "accept":
            return ("Применение собранного конфига запущено в фоне: один перезапуск router, на проде около 4–8 минут без соединений через РФ-ноду. "
                    "Ход и итог — в блоке «Применение на router» на этой странице. Повторно кнопку не нажимайте.", "info")
        return ("Собранный конфиг отклонён, рабочий конфиг router не менялся. Изменения в правилах / нодах остаются неприменёнными: при следующем «Применить» "
                "конфиг будет собран снова.", "success")
    if result.get("reason") == "busy":
        return ("Другое применение router ещё идёт — подождите его окончания.", "warning")
    return ("Не удалось запустить: " + str(result.get("detail") or result.get("reason") or "неизвестная ошибка")[:300], "danger")


def flash_for(result: dict, pl: dict | None = None, saved: bool = False) -> tuple[str, str]:
    """(text, category) for the flash message after an "Apply" button was pressed. `pl` = the plan that was computed before; `saved` = the settings
    were saved by the same button."""
    prefix = "Настройки сохранены. " if saved else ""
    where = "Ход и итог — в блоке «Применение на router» на этой странице."
    if result.get("started"):
        act = (pl or {}).get("action")
        if act == "countries":
            return (prefix + "Применение запущено в фоне: один перезапуск router, на проде около 4–8 минут без соединений через РФ-ноду. " + where, "info")
        if act == "full":
            return (prefix + "Применение запущено в фоне: изменены " + ", ".join(PART_TEXT.get(k, k) for k in (pl or {}).get("changed", [])) + ". Это полный цикл: новый конфиг "
                    "готовится в фоне (на проде около 15–20 минут, router работает как раньше), затем один перезапуск router ≈ 4–8 минут без соединений через РФ-ноду. "
                    "Повторно кнопку не нажимайте. " + where, "info")
        return (prefix + "Применение запущено в фоне. " + where, "info")
    reason = result.get("reason")
    if reason == "nothing":
        return (prefix + "Всё уже применено — перезапуск router не нужен.", "success")
    if reason == "busy":
        return (prefix + "Применение router уже идёт — второе не запускается. Дождитесь окончания. " + where, "warning")
    return (prefix + "Применение router НЕ запущено: " + str(result.get("detail") or reason or "неизвестная ошибка")[:300]
            + ". Можно выполнить на сервере: multiloc_ctl.py apply --auto", "danger")
