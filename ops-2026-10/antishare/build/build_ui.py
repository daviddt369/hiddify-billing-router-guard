"""builds the UI whitelist for shared (CDN) addresses from the production originals: usage build_ui.py   (reads orig/ + orig_ui/, writes payload/)
Every replacement asserts its anchor, line endings of each file are kept."""
import ast
import difflib
import os

ROOT = os.path.dirname(os.path.abspath(__file__))


def rd(path):
    raw = open(path, "rb").read()
    return raw.decode("utf-8").replace("\r\n", "\n"), b"\r\n" in raw


def wr(path, text, crlf):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def sub(text, old, new, count=1):
    assert old in text, "anchor not found: " + old[:70]
    return text.replace(old, new, count)


def diff(name, a, b):
    d = "\n".join(difflib.unified_diff(a.splitlines(), b.splitlines(), f"a/{name}", f"b/{name}", lineterm="", n=2)) + "\n"
    open(os.path.join(ROOT, "patch", name.replace("/", "_") + ".diff"), "w", encoding="utf-8", newline="\n").write(d)


# ---------------------------------------------------------------- models.py: one more table
s, crlf = rd(f"{ROOT}/orig_ui/models.py")
orig = s
s = s.rstrip("\n") + '''


class AntiShareSharedIP(db.Model):
    """addresses of shared infrastructure (CDN edges) added in the admin page: they are not a user's device and are never banned (see antishare/shared.py)"""
    __tablename__ = "anti_share_shared_ip"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    cidr = db.Column(db.String(64), nullable=False, unique=True)
    note = db.Column(db.String(200), default="", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, nullable=False)
'''
ast.parse(s)
wr(f"{ROOT}/payload/antishare/models.py", s, crlf)
diff("antishare/models.py", orig, s)

# ---------------------------------------------------------------- shared.py: merge the file list with the table
p = f"{ROOT}/payload/antishare/shared.py"
s = open(p, encoding="utf-8").read()
if "def all_networks" not in s:
    s = s.rstrip("\n") + '''


def db_networks() -> list:
    """the addresses added in the admin page (table anti_share_shared_ip); any problem with the table means "none" - the cycle must never fail because of this list"""
    try:
        from hiddifypanel.antishare.models import AntiShareSharedIP
        return parse_networks({"cidrs": [r.cidr for r in AntiShareSharedIP.query.all()]})
    except Exception:
        return []


def all_networks() -> list:
    return load_networks() + db_networks()
'''
    ast.parse(s)
    open(p, "w", encoding="utf-8", newline="\n").write(s)

# ---------------------------------------------------------------- runner.py: the table is created with the others, the cycle uses both lists
s, crlf = rd(f"{ROOT}/payload/antishare/runner.py")
orig_runner, _ = rd(f"{ROOT}/orig/runner.py")
if "AntiShareSharedIP" not in s:
    s = sub(s, "from hiddifypanel.antishare.models import AntiShareConfig, AntiShareEvent, AntiShareIPProfile, AntiShareState, AntiShareUserOverride\n",
            "from hiddifypanel.antishare.models import AntiShareConfig, AntiShareEvent, AntiShareIPProfile, AntiShareSharedIP, AntiShareState, AntiShareUserOverride\n")
    s = sub(s, "        AntiShareUserOverride.__table__,\n    ):", "        AntiShareUserOverride.__table__,\n        AntiShareSharedIP.__table__,\n    ):")
    s = sub(s, "from hiddifypanel.antishare.shared import load_networks, split_shared\n", "from hiddifypanel.antishare.shared import all_networks, split_shared\n")
    s = sub(s, "split_shared(current_ips, load_networks())", "split_shared(current_ips, all_networks())")
    ast.parse(s)
    wr(f"{ROOT}/payload/antishare/runner.py", s, crlf)
diff("antishare/runner.py", orig_runner, s)

# ---------------------------------------------------------------- AntiShareAdmin.py
s, crlf = rd(f"{ROOT}/orig_ui/AntiShareAdmin.py")
orig_admin = s
s = sub(s, "import json\nimport datetime\n", "import json\nimport datetime\nimport ipaddress\n")
s = sub(s, "from hiddifypanel.antishare.models import AntiShareConfig, AntiShareEvent, AntiShareIPProfile, AntiShareState, AntiShareUserOverride\n",
        "from hiddifypanel.antishare.models import AntiShareConfig, AntiShareEvent, AntiShareIPProfile, AntiShareSharedIP, AntiShareState, AntiShareUserOverride\n"
        "from hiddifypanel.antishare.shared import load_networks\n")
s = sub(s, "    def _override_row(self, user_id: int) -> AntiShareUserOverride:", '''    def _shared_context(self) -> dict:
        rows = AntiShareSharedIP.query.order_by(AntiShareSharedIP.id.asc()).all()
        return {"shared_rows": rows, "shared_file_count": len(load_networks())}

    def _handle_shared_action(self, action: str) -> None:
        """white list of shared addresses (CDN edges): they do not count as a user's device and are never banned"""
        if action == "shared_remove":
            try:
                row = AntiShareSharedIP.query.filter(AntiShareSharedIP.id == int(request.form.get("shared_id") or 0)).first()
            except ValueError:
                row = None
            if row:
                db.session.delete(row)
                db.session.commit()
                hutils.flask.flash(f"Адрес {row.cidr} убран из списка общих адресов.", "success")
            return
        raw = (request.form.get("shared_cidr") or "").strip()
        note = (request.form.get("shared_note") or "").strip()[:200]
        try:
            net = ipaddress.ip_network(raw, strict=False)
        except ValueError:
            hutils.flask.flash("Не похоже на IP-адрес или диапазон (пример: 198.51.100.151 или 198.51.100.0/24).", "danger")
            return
        if (net.version == 4 and net.prefixlen < 16) or (net.version == 6 and net.prefixlen < 32):
            hutils.flask.flash("Слишком широкий диапазон: так можно случайно выключить учёт для всех. Минимум /16 для IPv4 и /32 для IPv6.", "danger")
            return
        cidr = str(net)
        if AntiShareSharedIP.query.filter(AntiShareSharedIP.cidr == cidr).first():
            hutils.flask.flash(f"{cidr} уже есть в списке.", "warning")
            return
        db.session.add(AntiShareSharedIP(cidr=cidr, note=note))
        db.session.commit()
        hutils.flask.flash(f"{cidr} добавлен: он больше не считается устройством пользователя и не банится.", "success")

    def _override_row(self, user_id: int) -> AntiShareUserOverride:''')
s = sub(s, '''        action = (request.form.get("action") or "").strip().lower()
        if action:
            self._handle_user_action()''', '''        action = (request.form.get("action") or "").strip().lower()
        if action in ("shared_add", "shared_remove"):
            self._handle_shared_action(action)
            return redirect(request.path)
        if action:
            self._handle_user_action()''')
n = s.count("            user_rows=self._user_rows(),\n        )") + s.count("                user_rows=self._user_rows(),\n            )")
s = s.replace("            user_rows=self._user_rows(),\n        )", "            user_rows=self._user_rows(),\n            **self._shared_context(),\n        )")
s = s.replace("                user_rows=self._user_rows(),\n            )", "                user_rows=self._user_rows(),\n                **self._shared_context(),\n            )")
assert s.count("**self._shared_context()") == 3, s.count("**self._shared_context()")
ast.parse(s)
wr(f"{ROOT}/payload/panel_admin/AntiShareAdmin.py", s, crlf)
diff("panel/admin/AntiShareAdmin.py", orig_admin, s)

# ---------------------------------------------------------------- template
s, crlf = rd(f"{ROOT}/orig_ui/anti-share-settings.html")
orig_tpl = s
block = '''  <div class="box box-default">
    <div class="box-header with-border">
      <h3 class="box-title">Общие адреса (CDN): не считаются устройствами пользователя</h3>
    </div>
    <div class="box-body">
      <p class="text-muted">Граничные узлы CDN (например G-Core) видны сразу у многих пользователей. Такой адрес не учитывается как устройство и никогда не банится, иначе пострадали бы все, кто идёт через этот узел.
        Официальный список G-Core загружен из файла: <b>{{ shared_file_count }}</b> диапазонов (обновляется отдельно). Если G-Core добавит узлы, впишите адрес или диапазон здесь.</p>
      {% if shared_rows %}
      <table class="table table-bordered table-striped">
        <thead><tr><th>Адрес или диапазон</th><th>Заметка</th><th>Добавлен (UTC)</th><th></th></tr></thead>
        <tbody>
          {% for r in shared_rows %}
          <tr>
            <td><code>{{ r.cidr }}</code></td>
            <td>{{ r.note }}</td>
            <td>{{ r.created_at.strftime('%Y-%m-%d %H:%M') if r.created_at else '' }}</td>
            <td>
              <form method="post" style="display:inline-block;">
                {{ form.csrf_token }}
                <input type="hidden" name="action" value="shared_remove">
                <input type="hidden" name="shared_id" value="{{ r.id }}">
                <button type="submit" class="btn btn-xs btn-default">Убрать</button>
              </form>
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      {% else %}
      <p class="text-muted">Своих адресов пока нет.</p>
      {% endif %}
      <form method="post" class="form-inline">
        {{ form.csrf_token }}
        <input type="hidden" name="action" value="shared_add">
        <input type="text" name="shared_cidr" class="form-control" style="min-width:260px;" placeholder="198.51.100.151 или 198.51.100.0/24" required>
        <input type="text" name="shared_note" class="form-control" style="min-width:260px;" placeholder="заметка (например: G-Core, новый узел)">
        <button type="submit" class="btn btn-primary">Добавить</button>
      </form>
    </div>
  </div>

'''
anchor = '  <div class="box box-default">\n    <div class="box-header with-border">\n      <h3 class="box-title">Пользователи</h3>'
s = sub(s, anchor, block + anchor)
wr(f"{ROOT}/payload/templates/anti-share-settings.html", s, crlf)
diff("templates/anti-share-settings.html", orig_tpl, s)
print("built:", sorted(os.listdir(f"{ROOT}/payload")))
