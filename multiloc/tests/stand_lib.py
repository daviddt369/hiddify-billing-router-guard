"""Shared helpers for the stand test-suite. Runs INSIDE the stand (root). Everything talks to REAL processes:
panel (HTTP), MariaDB, Redis, HAProxy, hiddify-xray, xray-router, the stand exits, and real xray *clients* built from subscription links.
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, "/work/tools/dp")
import stand_client as sc  # noqa: E402

PC = "stCli0Path1234567890abcdef"                       # proxy_path_client (synthetic, deterministic)
USERS = {
    "active": "11111111-1111-4111-8111-111111111111",
    "tester": "22222222-2222-4222-8222-222222222222",
    "expired": "33333333-3333-4333-8333-333333333333",
    "overlimit": "44444444-4444-4444-8444-444444444444",
    "disabled": "55555555-5555-4555-8555-555555555555",
}
VENV_PY = "/opt/hiddify-manager/.venv313/bin/python"
CTL = [VENV_PY, "/opt/hiddify-manager/scripts/multiloc_ctl.py"]
FOREIGN = "http://foreign-site.test:18080/"
FOREIGN_IP = "http://13.0.0.20:18080/"
RU_DOMAIN = "http://ru-site.test:18080/"
RU_CIDR = "http://12.0.0.20:18080/"
RESULTS = "/work/out/results"


def sh(cmd, check=True, timeout=300, **kw):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout, **kw)
    if check and r.returncode != 0:
        raise RuntimeError(f"command failed rc={r.returncode}: {cmd}\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    return r


def mysql(sql, db="hiddifypanel"):
    return sh(["mysql", "-N", "-B", db, "-e", sql]).stdout.strip()


def ctl(*args, check=True):
    r = sh(CTL + [str(a) for a in args], check=check, cwd="/opt/hiddify-manager/hiddify-panel")
    return r.stdout


def systemd(action, *units, check=True):
    return sh(["systemctl", action, *units], check=check)


def is_active(unit):
    return sh(["systemctl", "is-active", unit], check=False).stdout.strip() == "active"


def flush_cache():
    rp = sh("grep '^requirepass' /opt/hiddify-manager/other/redis/redis.conf | awk '{print $2}'").stdout.strip()
    sh(["redis-cli", "flushall"], env={**os.environ, "REDISCLI_AUTH": rp})


def sub_text(user="tester", fmt="sub/", ua="Happ/3.5.0", host="ru.stand.test", uuid=None):   # AUTO / countries are for Happ and INCY only
    return sc.fetch_subscription(PC, uuid or USERS[user], host=host, fmt=fmt, ua=ua)


def all_configs(user="tester", uuid=None):
    """the user panel's own "All Configs" list (the page calls this API): the usual subscription links never carry countries / AUTO, this list does"""
    req = urllib.request.Request(f"{sc.SUB_BASE}/{PC}/api/v2/user/all-configs/",
                                 headers={"Host": "ru.stand.test", "Hiddify-API-Key": uuid or USERS[user], "User-Agent": "Mozilla/5.0 Chrome/135"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def links(user="tester", uuid=None, **kw):
    """[(profile name, vless:// / trojan:// link)] of every xray-style proxy the user panel lists - legacy profiles and, for testers, the countries.
    Names are the link fragments (flag, label, RU_GLOBAL ...), exactly what the apps show."""
    out = []
    for i in all_configs(user, uuid):
        link = i.get("link") or ""
        if link.startswith(("vless://", "trojan://")) and "#" in link:
            out.append((urllib.parse.unquote(link.split("#", 1)[1]), link))
    return out


def all_names(user="tester", uuid=None, **kw):
    """profile names of EVERY scheme (vless, trojan, tuic, hysteria2, naive, mieru, ...) of the user panel's list, in order"""
    out = []
    for i in all_configs(user, uuid):
        link = i.get("link") or ""
        if i.get("domain") != "ALL" and "://" in link and "#" in link:
            out.append(urllib.parse.unquote(link.split("#", 1)[1]))
    return out


def pick(links_, *needles, exclude=()):
    """first (name, link) whose name contains every needle (and none of `exclude`)"""
    for name, link in links_:
        if all(n in name for n in needles) and not any(x in name for x in exclude):
            return name, link
    raise LookupError(f"no profile with {needles}; have: {[n for n, _ in links_][:30]}")


def via(link, url=FOREIGN, timeout=10.0):
    """real xray client -> HAProxy -> hiddify-xray -> router -> exit.  returns 'exit-a' | 'exit-b' | 'direct' ; None on failure"""
    try:
        with sc.Client(link) as c:
            st, body = c.get(url, timeout=timeout)
            return body.get("via") if isinstance(body, dict) else None
    except Exception:
        return None


def via_many(link, n, url=FOREIGN, timeout=10.0):
    out = []
    try:
        with sc.Client(link) as c:
            for _ in range(n):
                try:
                    st, body = c.get(url, timeout=timeout)
                    out.append(body.get("via") if isinstance(body, dict) else None)
                except Exception:
                    out.append(None)
    except Exception:
        return [None] * n
    return out


def router_direct(url, timeout=6.0, port=20808):
    """talk to a router entrance without hiddify (socks <port>)"""
    r = subprocess.run(["curl", "-sS", "--max-time", str(timeout), "--socks5-hostname", f"127.0.0.1:{port}", url],
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout).get("via") if r.returncode == 0 else None
    except Exception:
        return None


def wait_until(fn, timeout=40.0, step=1.0, what="condition"):
    t0 = time.monotonic()
    last = None
    while time.monotonic() - t0 < timeout:
        try:
            last = fn()
            if last:
                return last
        except Exception as e:  # noqa
            last = e
        time.sleep(step)
    raise TimeoutError(f"timeout waiting for {what}: last={last!r}")


def set_delay(exit_name, ms):
    with open(f"/run/stand/delay-{exit_name}", "w") as f:
        f.write(str(int(ms)))


def router_ready(timeout=45):
    """wait until the router restarted, observatory has results and at least one exit answers on the default entrance"""
    return wait_until(lambda: router_direct(FOREIGN_IP) in ("exit-a", "exit-b"), timeout=timeout, what="router ready")


def apply_router():
    out = ctl("apply")
    router_ready()
    return out


def reset_locations_to_stand_default():
    """canonical test state: Germany=slot1(exit A nodes 1,2) USA=slot2(exit B nodes 3,4) AUTO on, tester only, slots 3/4 empty"""
    # normalise by SQL first (earlier tests may have left rows the CLI validation would refuse to touch one by one)
    mysql("UPDATE commercial_routing_location SET enabled=0, title='', code='', country_iso='', flag='', visibility='testers' "
          "WHERE kind='country'; DELETE FROM commercial_routing_location WHERE kind='auto'; DELETE FROM commercial_routing_location_node; "
          "DELETE FROM commercial_routing_location_tester")
    ctl("set", 1, "--title", "Германия", "--iso", "DE", "--code", "de", "--enabled", 1, "--visibility", "testers", "--order", 30)
    ctl("set", 2, "--title", "США", "--iso", "US", "--code", "us", "--enabled", 1, "--visibility", "testers", "--order", 40)
    ctl("set", 3, "--title", "", "--iso", "", "--code", "", "--enabled", 0, "--visibility", "testers")
    ctl("set", 4, "--title", "", "--iso", "", "--code", "", "--enabled", 0, "--visibility", "testers")
    ctl("auto", "--title", "AUTO", "--enabled", 1, "--visibility", "testers", "--order", 10)
    ctl("assign", 1, 1, 2)
    ctl("assign", 2, 3, 4)
    ctl("tester", "add", USERS["tester"], "--note", "stand tester")
    flush_cache()


def panel_python(code: str, timeout=120):
    """run python inside the panel's app context (real DB, real hiddifypanel code). prints are returned"""
    full = (
        "import os,sys\n"
        "os.environ.setdefault('HIDDIFY_CFG_PATH','/opt/hiddify-manager/hiddify-panel/app.cfg')\n"
        "os.environ.setdefault('HIDDIFY_CONFIG_PATH','/opt/hiddify-manager/')\n"
        "os.chdir('/opt/hiddify-manager/hiddify-panel')\n"
        "import sqlalchemy, hiddifypanel.panel.init_db as _i\n"
        "_i.text = getattr(_i, 'text', sqlalchemy.text)\n"
        "from hiddifypanel import create_app\n"
        "app = create_app(app_mode='cli')\n"
        "ctx = app.app_context(); ctx.push()\n"
    ) + code
    r = subprocess.run([VENV_PY, "-c", full], capture_output=True, text=True, timeout=timeout, cwd="/opt/hiddify-manager/hiddify-panel")
    lines = [ln for ln in r.stdout.splitlines() if "upgrade_database" not in ln and "migrating_db" not in ln]
    if r.returncode != 0:
        raise RuntimeError("panel_python failed:\n" + r.stderr[-2500:] + "\n".join(lines[-20:]))
    return "\n".join(lines)


STAND_SUDOERS = "/etc/sudoers.d/92-stand-mirror-prod-hiddify"


def ensure_stand_sudoers():
    """TEST INFRASTRUCTURE, inside the stand only: production's /etc/sudoers.d/hiddify lets the panel user run commander.py with ANY arguments, the stand
    image has only the narrow `commercial-routing-apply` rule. Mirror production so the panel -> sudo -> commander.py path is tested for real. Idempotent."""
    want = ("# STAND ONLY (test infrastructure, never installed on a server by the multiloc package):\n"
            "# mirrors production's /etc/sudoers.d/hiddify - the panel user may run commander.py with any arguments\n"
            "hiddify-panel ALL=(root) NOPASSWD: /opt/hiddify-manager/common/commander.py\n")
    try:
        if open(STAND_SUDOERS).read() == want:
            return
    except OSError:
        pass
    tmp = STAND_SUDOERS + ".tmp"
    with open(tmp, "w") as f:
        f.write(want)
    os.chmod(tmp, 0o440)
    sh(["visudo", "-cf", tmp])
    os.replace(tmp, STAND_SUDOERS)


# ------------------------------------------------------------------------------------------------ the single "Apply" of the routing area
PROBE_RULE_COMMENT = "stand-apply-probe"


def plan_now() -> dict:
    """what the single Apply would do right now (the panel's own function, real database, real router config)"""
    out = panel_python("import json\nfrom hiddifypanel.hutils import multiloc_apply as m\nprint(json.dumps(m.plan()))")
    return json.loads(out.splitlines()[-1])


def ensure_baseline(force=False):
    """the router reflects the database NOW (call right after apply_router() on the canonical state): remember it as 'applied'"""
    if force or not os.path.exists("/var/lib/multiloc-router-apply/applied.json"):
        ctl("baseline")


_AUTOINC = {}


def autoinc(table):
    return int(mysql(f"select auto_increment from information_schema.tables where table_schema='hiddifypanel' and table_name='{table}'") or 0)


def remember_autoinc(table):
    """a test that inserts rows into a legacy table and deletes them again must also give the auto-increment counter back: the byte-level state check of the series
    compares SHOW CREATE TABLE, which prints it"""
    _AUTOINC.setdefault(table, autoinc(table))


def give_back_autoinc(table):
    if table in _AUTOINC:
        mysql(f"alter table {table} auto_increment={_AUTOINC.pop(table)}")


def restart_panel():
    """after a test stopped the database the panel's pooled connections are dead (the first requests answer 500): restart it"""
    sh(["systemctl", "restart", "hiddify-panel"])
    wait_until(lambda: sh("curl -s -o /dev/null -m 3 -w '%{http_code}' http://127.0.0.1:9000/ || true", check=False).stdout.strip() not in ("", "000"), timeout=90, what="panel")
    time.sleep(3)
    flush_cache()


def add_probe_rule(value="apply-probe.example"):
    """a legacy-side change (a custom routing rule)"""
    remember_autoinc("commercial_routing_custom_rule")
    mysql("insert into commercial_routing_custom_rule (rule_type, value, normalized_value, outbound_policy, enabled, comment, created_at, updated_at) "
          f"values ('domain_suffix','{value}','{value}','direct',1,'{PROBE_RULE_COMMENT}',now(),now())")


def drop_probe_rules():
    mysql(f"delete from commercial_routing_custom_rule where comment='{PROBE_RULE_COMMENT}'")
    give_back_autoinc("commercial_routing_custom_rule")


def router_starts_since(since_iso):
    """how many times xray-router was (re)started since the given 'YYYY-MM-DD HH:MM:SS' (journal)"""
    out = sh(["journalctl", "-u", "xray-router", "-o", "cat", "--no-pager", "--since", since_iso], check=False).stdout
    return out.count("Started xray-router")


def now_iso():
    return time.strftime("%Y-%m-%d %H:%M:%S")


_CONVERGED = {"done": False}


def converge_pipeline():
    """the running router config = what the full pipeline builds from the database. A full cycle whose result differs from the running config in more than the pending change is
    KEPT for the owner's decision (rc 4), and the dev stand starts from an older prod-like config - so the tests align it once (console `--accept-diff`)."""
    if not _CONVERGED["done"]:
        sh(CTL + ["apply", "--full", "--accept-diff"], check=False, timeout=1800, cwd="/opt/hiddify-manager/hiddify-panel")
        _CONVERGED["done"] = True


def prepare_apply_tests():
    """module set-up of every test that presses an Apply button: sudoers mirror of production, canonical locations, a known 'applied' baseline"""
    ensure_stand_sudoers()
    reset_locations_to_stand_default()
    apply_router()
    converge_pipeline()
    ensure_baseline(force=True)
