"""Migration / MariaDB tests (production versions: MariaDB 10.11.14, tables created by the REAL installer scripts, db_version 136).
SQLite is never used here. Destructive parts work on a SCRATCH database or restore the data from a dump they take first.

Run: /opt/hiddify-manager/.venv313/bin/python -W ignore -m unittest -v test_05_migration   (from /work/tests)"""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/work/tools")
from stand_lib import *  # noqa: E402,F403

SCRATCH = "hiddifypanel_scratch"
TABLES = ["commercial_routing_location", "commercial_routing_location_node", "commercial_routing_location_tester"]


def q(sql, db="hiddifypanel", check=True):
    r = sh(["mysql", "-N", "-B", db, "-e", sql], check=check)
    return r.stdout.strip()


def ddl_from_module():
    out = panel_python("from hiddifypanel.hutils import multiloc as m; import json; print('JSON' + json.dumps(m._DDL))")
    return json.loads(out.split("JSON", 1)[1])


def setUpModule():
    reset_locations_to_stand_default()
    assert q("select version()").startswith("10.11.14"), "tests must run on MariaDB 10.11.14 (production version)"


class TestProductionDatabase(unittest.TestCase):
    def test_server_is_the_production_mariadb(self):
        self.assertEqual(q("select version()").split("-")[0], "10.11.14")
        self.assertEqual(q("select @@character_set_database,@@collation_database"), "utf8mb4\tutf8mb4_unicode_ci")
        self.assertEqual(q("select value from str_config where `key`='db_version'"), "136")

    def test_tables_use_innodb_utf8mb4_unicode_ci_like_the_rest(self):
        rows = q("select table_name, engine, table_collation from information_schema.tables where table_schema='hiddifypanel' "
                 "and table_name in ('commercial_routing_upstream','commercial_routing_location','commercial_routing_location_node',"
                 "'commercial_routing_location_tester')").splitlines()
        got = {r.split("\t")[0]: r.split("\t")[1:] for r in rows}
        self.assertEqual(set(got), {"commercial_routing_upstream", *TABLES})
        for t, (eng, coll) in got.items():
            self.assertEqual((eng, coll), ("InnoDB", "utf8mb4_unicode_ci"), t)

    def test_foreign_keys_and_unique_constraints(self):
        fks = q("select constraint_name, table_name, referenced_table_name, delete_rule from information_schema.referential_constraints "
                "where constraint_schema='hiddifypanel' and constraint_name like 'fk_crln_%' order by 1").splitlines()
        self.assertEqual([f.split("\t") for f in fks], [
            ["fk_crln_location", "commercial_routing_location_node", "commercial_routing_location", "CASCADE"],
            ["fk_crln_upstream", "commercial_routing_location_node", "commercial_routing_upstream", "CASCADE"]])
        uniq = q("select index_name, non_unique from information_schema.statistics where table_schema='hiddifypanel' and "
                 "table_name='commercial_routing_location' and column_name='slot'")
        self.assertEqual(uniq, "ux_crl_slot\t0")

    def test_cascade_when_an_upstream_is_deleted_by_the_existing_admin_ui(self):
        """RoutingUpstreamAdmin deletes upstream rows with plain DELETE; the node mapping must disappear with it (no orphans)"""
        q("insert into commercial_routing_upstream (name,label,tunnel_type) values ('tmp-cascade','x','test_blackhole')")
        uid = q("select id from commercial_routing_upstream where name='tmp-cascade'")
        slot3 = q("select id from commercial_routing_location where slot=3")
        q(f"insert into commercial_routing_location_node (upstream_id, location_id) values ({uid},{slot3})")
        self.assertEqual(q(f"select count(*) from commercial_routing_location_node where upstream_id={uid}"), "1")
        q(f"delete from commercial_routing_upstream where id={uid}")
        self.assertEqual(q(f"select count(*) from commercial_routing_location_node where upstream_id={uid}"), "0")

    def test_integrity_constraints_reject_bad_rows(self):
        r = sh(["mysql", "hiddifypanel", "-e", "insert into commercial_routing_location (slot, kind) values (1,'country')"], check=False)
        self.assertNotEqual(r.returncode, 0, "duplicate slot must be rejected")
        r = sh(["mysql", "hiddifypanel", "-e", "insert into commercial_routing_location_node (upstream_id, location_id) values (999999, 1)"], check=False)
        self.assertNotEqual(r.returncode, 0, "node for a non-existing upstream must be rejected")
        r = sh(["mysql", "hiddifypanel", "-e", "insert into commercial_routing_location_node (upstream_id, location_id) values (1, 999999)"], check=False)
        self.assertNotEqual(r.returncode, 0, "node for a non-existing location must be rejected")

    def test_cyrillic_and_emoji_survive_a_roundtrip_through_the_cli_and_the_subscription(self):
        title = "Германия ✨ 日本"
        ctl("set", 1, "--title", title, "--flag", "🇩🇪")
        try:
            self.assertEqual(q("select title from commercial_routing_location where slot=1"), title)
            self.assertTrue(any(f"🇩🇪 {title}" in n for n in all_names("tester")), all_names("tester"))
        finally:
            ctl("set", 1, "--title", "Германия", "--flag", "")


class TestSchemaScripts(unittest.TestCase):
    """the DDL strings of the feature, executed on a scratch database that has the prod upstream table (created by the installer script)"""

    @classmethod
    def setUpClass(cls):
        q(f"drop database if exists {SCRATCH}", db="mysql")
        q(f"create database {SCRATCH} character set utf8mb4 collate utf8mb4_unicode_ci", db="mysql")
        # prod DDL of the upstream table, copied from the live (installer-made) table
        ddl = sh(f"mysql -N -B hiddifypanel -e 'show create table commercial_routing_upstream'").stdout.split("\t", 1)[1].replace("\\n", "\n")
        sh(["mysql", SCRATCH, "-e", ddl])
        cls.ddls = ddl_from_module()

    @classmethod
    def tearDownClass(cls):
        q(f"drop database if exists {SCRATCH}", db="mysql")

    def test_create_is_idempotent_and_additive(self):
        for _ in range(3):
            for d in self.ddls:
                sh(["mysql", SCRATCH, "-e", d])
        tabs = q("show tables", db=SCRATCH).split()
        self.assertEqual(set(tabs), {"commercial_routing_upstream", *TABLES})

    def test_creating_the_tables_does_not_touch_the_existing_table(self):
        before = sh(["mysql", "-N", "-B", SCRATCH, "-e", "show create table commercial_routing_upstream"]).stdout
        for d in self.ddls:
            sh(["mysql", SCRATCH, "-e", d])
        after = sh(["mysql", "-N", "-B", SCRATCH, "-e", "show create table commercial_routing_upstream"]).stdout
        self.assertEqual(before, after)

    def test_ddl_matches_the_running_schema_exactly(self):
        for d in self.ddls:
            sh(["mysql", SCRATCH, "-e", d])
        for t in TABLES:
            a = sh(["mysql", "-N", "-B", SCRATCH, "-e", f"show create table {t}"]).stdout
            b = sh(["mysql", "-N", "-B", "hiddifypanel", "-e", f"show create table {t}"]).stdout
            self.assertEqual(re.sub(r"AUTO_INCREMENT=\d+ ", "", a), re.sub(r"AUTO_INCREMENT=\d+ ", "", b), t)

    def test_ddl_works_when_the_upstream_table_is_missing_via_the_no_fk_fallback(self):
        """ensure_schema() retries without foreign keys if a constraint cannot be created (e.g. exotic engine on an old install)"""
        q(f"drop database if exists {SCRATCH}_nofk", db="mysql")
        q(f"create database {SCRATCH}_nofk character set utf8mb4 collate utf8mb4_unicode_ci", db="mysql")
        try:
            r = sh(["mysql", f"{SCRATCH}_nofk", "-e", self.ddls[1]], check=False)
            self.assertNotEqual(r.returncode, 0, "node table needs the upstream table (this is what triggers the fallback in ensure_schema)")
            out = panel_python("from hiddifypanel.hutils import multiloc as m; import json; print('JSON' + json.dumps(m.strip_foreign_keys(m._DDL[1])))")
            stripped = json.loads(out.split("JSON", 1)[1])
            self.assertNotIn("FOREIGN", stripped.upper())
            self.assertIn("KEY ix_crln_location (location_id)\n)", stripped)
            sh(["mysql", f"{SCRATCH}_nofk", "-e", stripped])
            self.assertIn("commercial_routing_location_node", q("show tables", db=f"{SCRATCH}_nofk"))
        finally:
            q(f"drop database if exists {SCRATCH}_nofk", db="mysql")


class TestFeatureToggleSafety(unittest.TestCase):
    """new code installed but tables absent (before init / after a manual DROP): nothing may break"""

    def test_panel_and_templates_work_without_the_tables_and_recover_after_restore(self):
        dump = "/work/out/feature-tables-test.sql"
        sh(f"mysqldump --single-transaction hiddifypanel {' '.join(TABLES)} > {dump}")
        golden_before = {n for n, _ in links("tester")}
        sh(f"mysql hiddifypanel -e 'DROP TABLE commercial_routing_location_tester, commercial_routing_location_node, commercial_routing_location'")
        try:
            flush_cache()
            names = all_names("tester")
            self.assertEqual(len([n for n in names if n.startswith("RU_GLOBAL")]), 15, "legacy subscription without the tables")
            self.assertFalse(any("AUTO" in n or "США" in n for n in names))
            out = sh(["bash", "-c", "source /opt/hiddify-manager/common/utils.sh; reload_all_configs >/dev/null; echo rc=$?"], cwd="/opt/hiddify-manager")
            self.assertIn("rc=0", out.stdout)
            self.assertEqual(json.load(open("/opt/hiddify-manager/current.json"))["locations"], [])
            sh(["bash", "/work/tools/stand_render.sh", "apply_users"], check=False)
            r = sh([XRAY, "run", "-test", "-confdir", "/opt/hiddify-manager/xray/configs/"], check=False)
            self.assertIn("Configuration OK", r.stdout + r.stderr)
            inb = json.load(open("/opt/hiddify-manager/xray/configs/05_inbounds_30_locations.json"))
            self.assertEqual(inb["inbounds"], [])
        finally:
            sh(f"mysql hiddifypanel < {dump}")
            flush_cache()
            sh(["bash", "/work/tools/stand_render.sh", "apply_users"], check=False)
        self.assertEqual({n for n, _ in links("tester")}, golden_before, "after restoring the dump everything is back")
        self.assertEqual(len(json.load(open("/opt/hiddify-manager/xray/configs/05_inbounds_30_locations.json"))["inbounds"]), 12)

    def test_cli_init_recreates_missing_tables_and_keeps_existing_labels(self):
        before = q("select slot,title,path_xhttp from commercial_routing_location order by slot")
        out = json.loads(ctl("init").split("\n", 1)[1] if ctl("init").startswith("20") else ctl("init"))
        self.assertEqual(out["created_tables"], [])
        self.assertEqual(out["slots_created"], [])
        self.assertEqual(q("select slot,title,path_xhttp from commercial_routing_location order by slot"), before)

    def test_ensure_slots_is_safe_against_a_concurrent_second_run(self):
        import subprocess
        ps = [subprocess.Popen(CTL + ["init"], cwd="/opt/hiddify-manager/hiddify-panel", stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
              for _ in range(3)]
        rcs = [p.wait() for p in ps]
        self.assertEqual(rcs, [0, 0, 0])
        self.assertEqual(q("select count(*) from commercial_routing_location where kind='country'"), "4")


class TestBackupsAreRestorable(unittest.TestCase):
    def test_the_installers_pre_change_dump_restores_into_a_scratch_db(self):
        runs = sorted(os.listdir("/opt/hiddify-manager/multiloc-installer-backups"))
        dump = f"/opt/hiddify-manager/multiloc-installer-backups/{runs[-1]}/db-before.sql.gz"
        self.assertTrue(os.path.exists(dump), dump)
        q(f"drop database if exists {SCRATCH}_restore", db="mysql")
        q(f"create database {SCRATCH}_restore character set utf8mb4 collate utf8mb4_unicode_ci", db="mysql")
        try:
            sh(f"zcat {dump} | mysql {SCRATCH}_restore")
            live = set(q("show tables").split()) - set(TABLES)
            restored = set(q("show tables", db=f"{SCRATCH}_restore").split())
            self.assertEqual(live, restored, "the dump contains exactly the pre-feature tables")
            for t in ("user", "domain", "proxy", "str_config", "bool_config", "commercial_routing_upstream", "commercial_routing_custom_rule"):
                self.assertEqual(q(f"select count(*) from {t}"), q(f"select count(*) from {t}", db=f"{SCRATCH}_restore"), t)
        finally:
            q(f"drop database if exists {SCRATCH}_restore", db="mysql")


XRAY = "/opt/hiddify-manager/xray/bin/xray"

if __name__ == "__main__":
    unittest.main(verbosity=2)
