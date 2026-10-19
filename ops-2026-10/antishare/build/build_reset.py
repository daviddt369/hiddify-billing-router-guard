"""second step on top of build_ui.py: (1) a CDN edge takes ONE slot of the user's limit but is never counted as "extra" (never banned),
(2) the "clear all statistics" button.  usage: build_reset.py   (reads and rewrites payload/, writes patch/*.diff against what build_ui.py produced)"""
import ast
import difflib
import os

ROOT = os.path.dirname(os.path.abspath(__file__))


def rd(path):
    raw = open(path, "rb").read()
    return raw.decode("utf-8").replace("\r\n", "\n"), b"\r\n" in raw


def wr(path, text, crlf):
    open(path, "wb").write((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def sub(text, old, new):
    assert old in text, "anchor not found: " + old[:70]
    return text.replace(old, new, 1)


def diff(name, a, b):
    d = "\n".join(difflib.unified_diff(a.splitlines(), b.splitlines(), f"a/{name}", f"b/{name}", lineterm="", n=2)) + "\n"
    open(os.path.join(ROOT, "patch", "reset_" + name.replace("/", "_") + ".diff"), "w", encoding="utf-8", newline="\n").write(d)


# ---------------------------------------------------------------- runner.py
p = f"{ROOT}/payload/antishare/runner.py"
s, crlf = rd(p)
orig = s
if "real_allowed" not in s:
    s = sub(s, '''    allowed_count = max(1, int(user.max_ips or 1))

    for profile in profiles.values():''', '''    allowed_count = max(1, int(user.max_ips or 1))
    # a CDN edge takes ONE slot of the limit (limit 3 = the edge + 2 other addresses), but it is only ever counted, never "extra": it cannot be banned.
    # With a limit of 1 the user still keeps one real address (a single device that switches between the CDN and a direct path must not be punished).
    shared_slot = 1 if shared_ips else 0
    real_allowed = max(1, allowed_count - shared_slot)

    for profile in profiles.values():''')
    s = sub(s, "pick_allowed_ips(current_ips, profiles, allowed_count, now, settings)", "pick_allowed_ips(current_ips, profiles, real_allowed, now, settings)")
    s = sub(s, "    excess = max(0, len(current_ips) - allowed_count)\n", "    excess = max(0, len(current_ips) - real_allowed)\n")
    s = sub(s, '            "shared_ips_ignored": len(shared_ips),\n', '            "shared_ips_ignored": len(shared_ips),\n            "real_allowed": real_allowed,\n')
    ast.parse(s)
    wr(p, s, crlf)
    diff("antishare/runner.py", orig, s)

# ---------------------------------------------------------------- AntiShareAdmin.py
p = f"{ROOT}/payload/panel_admin/AntiShareAdmin.py"
s, crlf = rd(p)
orig = s
if "_handle_reset_all" not in s:
    s = sub(s, "    def _override_row(self, user_id: int) -> AntiShareUserOverride:", '''    def _handle_reset_all(self) -> None:
        """clear ALL observations (events, address profiles, scores, warnings, blocks); a copy is kept in the database (see antishare/reset.py)"""
        if (request.form.get("reset_confirm") or "").strip().upper() != "ОЧИСТИТЬ":
            hutils.flask.flash("Статистика не очищена: нужно ввести слово ОЧИСТИТЬ.", "danger")
            return
        from hiddifypanel.antishare.reset import reset_all
        settings = AntiShareSettings.load()
        backend = NftBanBackend(helper_path=settings.nft_helper, enabled=settings.nft_enabled, dry_run=settings.nft_dry_run)
        try:
            r = reset_all(backend)
        except Exception as exc:
            db.session.rollback()
            hutils.flask.flash(f"Очистка не выполнена, данные не тронуты: {exc}", "danger")
            return
        hutils.flask.flash(
            f"Статистика очищена: событий {r['event_rows']}, адресов пользователей {r['profile_rows']}, состояний сброшено {r['state_rows']}, разбанено адресов {r['unbanned']}. "
            f"Копия до очистки сохранена в таблицах anti_share_bak_{r['stamp']}_* (хранится только последняя).", "success")

    def _override_row(self, user_id: int) -> AntiShareUserOverride:''')
    s = sub(s, '''        if action in ("shared_add", "shared_remove"):''', '''        if action == "reset_all":
            self._handle_reset_all()
            return redirect(request.path)
        if action in ("shared_add", "shared_remove"):''')
    ast.parse(s)
    wr(p, s, crlf)
    diff("panel/admin/AntiShareAdmin.py", orig, s)

# ---------------------------------------------------------------- template
p = f"{ROOT}/payload/templates/anti-share-settings.html"
s, crlf = rd(p)
orig = s
if "reset_all" not in s:
    block = '''  <div class="box box-danger">
    <div class="box-header with-border">
      <h3 class="box-title">Очистить всю статистику анти-шаринга</h3>
    </div>
    <div class="box-body">
      <p class="text-muted">Стирает все наблюдения: события, адреса пользователей, баллы, предупреждения и блокировки (активные баны снимаются). Настройки, отключения по пользователям и список общих адресов остаются.
        Перед очисткой копия сохраняется в базе (хранится только последняя). После очистки наблюдение идёт с нуля.</p>
      <form method="post" class="form-inline" onsubmit="return confirm('Стереть всю статистику анти-шаринга?');">
        {{ form.csrf_token }}
        <input type="hidden" name="action" value="reset_all">
        <input type="text" name="reset_confirm" class="form-control" style="min-width:220px;" placeholder="введите слово ОЧИСТИТЬ" autocomplete="off" required>
        <button type="submit" class="btn btn-danger">Очистить всё</button>
      </form>
    </div>
  </div>

'''
    anchor = '  <div class="box box-default">\n    <div class="box-header with-border">\n      <h3 class="box-title">Пользователи</h3>'
    s = sub(s, anchor, block + anchor)
    wr(p, s, crlf)
    diff("templates/anti-share-settings.html", orig, s)
print("built")
