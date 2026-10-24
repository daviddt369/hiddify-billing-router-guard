# CP-05 · fail-closed для каждой страны (закрывает F-02)

**Статус: подготовлено и проверено на стенде. На production НЕ применялось. Нужно согласование владельца.**

## 1. Что и зачем

Балансировщик страны (`balancer-s1` = DE_1, `balancer-s3` = NL_3) сейчас создаётся **без `fallbackTag`**. По документации и исходникам Xray, если у балансировщика нет ни одного живого узла и `fallbackTag` не задан, выбор не удаётся, и маршрутизатор использует **обработчик по умолчанию = ПЕРВЫЙ outbound файла** (`app/dispatcher: default route for …`). В живом конфиге router первый outbound — `upstream-16`, это узел **Нидерландов**, и он живой.

Следствие на проде уже сегодня:
* если лягут все узлы DE_1, профиль «Германия» **молча выйдет через Нидерланды** (нарушение правила «при падении страны без переключения на другую»);
* то же в первые мгновения после каждого перезапуска router (observatory ещё без результатов): за 7 суток на проде было 3 перезапуска.

Исправление: у каждого балансировщика страны `"fallbackTag": "block"` (в живом конфиге `block` уже есть и это `blackhole`). Без живых узлов соединение закрывается.
Затрагивает **только слой multiloc** (`multiloc_router.py`, один файл). `router_core.py`, `commercial_routing.py` и глобальный балансировщик `upstream-balancer` (AUTO) **не меняются**.

## 2. Доказательства (все на стенде, на проде ничего не запускалось)

| Проверка | Результат |
|---|---|
| Реальные Xray **26.7.28 (5ca6f4b, как на RU)**, 26.3.27 (как на .238), 26.2.6 (как на .190): мини-router формы прода, `fallbackTag` нет | **утечка** DE→NL, NL→DE и в окне старта (`../_evidence/F-02_default_handler_3_binaries.txt`) |
| Те же бинарники с `fallbackTag:"block"` | закрыто во всех трёх сценариях; контрольные (всё живо) не изменились |
| Полный стек стенда (настоящие xray-клиенты → HAProxy → hiddify-xray → router → выходы), новый тест «погасить США» **до** исправления | **КРАСНЫЙ**: профиль США выходит через `exit-a` (= Германию) — `test/RED-before-fix-dataplane.txt` |
| Тот же тест **после** исправления + 3 соседних теста отказов | зелёные (4 теста, 104 с) — `test/GREEN-after-fix.txt` |
| `test_01_unit.py` целиком | 43 теста OK, включая `xray run -test` реального рендера и проверку, что установщик видит слой так же, как раньше |
| **Конфиг формы прода** (живой конфиг RU, секреты заменены на валидные заглушки; план из БД) на Xray 26.7.28 | базовый слой считает живой конфиг «в синхроне»; новый слой меняет **ровно** `balancer-s1` и `balancer-s3` (+`fallbackTag`), больше ничего; инварианты OK; идемпотентно; `xray run -test` **полного** конфига до и после = `Configuration OK` — `test/prod-shaped-config-check.txt` |
| Доставка штатным `multiloc_delta_deploy.py` + `multiloc_ctl.py apply`, затем откат | сухой прогон: 1 файл; apply: 1 перезапуск router, повторный apply «no change»; откат возвращает прежний конфиг — `test/delta-deploy-and-rollback-rehearsal.txt`, `test/apply-and-rollback-scripts-on-stand.txt` (сами `apply.sh` и `rollback.sh`) |

Важное наблюдение: `xray run -test` **не проверяет**, что `fallbackTag` указывает на существующий outbound (отсутствующий тег даёт `Configuration OK`, проверено на 26.7.28). Поэтому единственный предохранитель — новый инвариант в `check_invariants` (outbound `block` существует и является blackhole). Он срабатывает до записи конфига; тест `test_invariants_catch_regressions` это покрывает.

## 3. Точный diff (production, один файл)

Файл: `/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/hutils/multiloc_router.py`,
sha256 до: `0ec2d27b2fe7315d…` (совпадает с продом, проверено read-only), после: `49e82682a6881621…`.

```diff
--- a/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/hutils/multiloc_router.py
+++ b/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/hutils/multiloc_router.py
@@ -2,7 +2,8 @@
 
 transform(config, plan) is a PURE function (unit-testable, idempotent):
   * adds one socks entrance per slot  (from-hiddify-s<N>, 127.0.0.1:<router_port>+2+N, same sniffing as the default entrance)
-  * adds one balancer per slot with >=2 live-capable nodes (leastPing, NO fallbackTag -> a dead country fails closed)
+  * adds one balancer per slot with >=2 live-capable nodes (leastPing, `fallbackTag: "block"` -> a dead country fails closed: without a fallbackTag Xray
+    hands a balancer that has no live node to its "default handler", which is the FIRST outbound of the file = some node of ANOTHER country)
   * inserts the slot final rules right BEFORE the generic catch-all, i.e. after every RU-direct / DNS / block rule that the
     existing layers produced, so RU traffic from any entrance still stays in Russia
   * duplicates the "global-upstream-bound" specific rules (e.g. proxy-DNS IPs -> upstream-balancer) per slot, so even those
@@ -28,6 +29,7 @@
 
 RUNTIME_PATH = "/etc/xray-router/config.json"
 GLOBAL_BALANCER = "upstream-balancer"
+FAIL_CLOSED_TAG = "block"           # blackhole outbound that a country balancer falls back to when none of its nodes is alive
 _SPECIFIC_KEYS = ("domain", "ip", "port", "inboundTag", "protocol", "user", "source", "sourcePort", "attrs")
 
 
@@ -114,7 +116,8 @@
             used_selector += tags
         else:
             bal = multiloc.balancer_tag(slot)
-            new_balancers.append({"tag": bal, "selector": tags, "strategy": copy.deepcopy(strategy)})  # no fallbackTag
+            # fallbackTag = blackhole: with no live node Xray must NOT use its default handler (the first outbound of the file = another country's node)
+            new_balancers.append({"tag": bal, "selector": tags, "strategy": copy.deepcopy(strategy), "fallbackTag": FAIL_CLOSED_TAG})
             target = {"balancerTag": bal}
             used_selector += tags
         slot_target[slot] = target
@@ -166,10 +169,14 @@
     # 2) slot finals precede the generic catch-all
     if ours and final_idx is not None and max(ours) > final_idx:
         problems.append("a slot final rule appears after the generic catch-all")
-    # 3) fail-closed: no slot balancer has a fallback
+    # 3) fail-closed: every country balancer falls back to the blackhole and to nothing else (a country never borrows another country's node)
     for tag, b in bals.items():
-        if tag.startswith("balancer-s") and b.get("fallbackTag"):
-            problems.append(f"{tag} has a fallbackTag (must fail closed)")
+        if tag.startswith("balancer-s") and b.get("fallbackTag") != FAIL_CLOSED_TAG:
+            problems.append(f"{tag}: fallbackTag must be '{FAIL_CLOSED_TAG}' to fail closed, got {b.get('fallbackTag')!r}")
+    if any(t.startswith("balancer-s") for t in bals):
+        block = next((o for o in config.get("outbounds", []) if o.get("tag") == FAIL_CLOSED_TAG), None)
+        if block is None or block.get("protocol") != "blackhole":
+            problems.append(f"outbound '{FAIL_CLOSED_TAG}' is missing or is not a blackhole: the country balancers cannot fail closed")
     # 4) slot rules only reference existing outbounds / balancers
     for r in rules:
         if _is_ours(r):
```

Тесты (на production не едут): `patch/test_01_unit.py.diff`, `patch/test_03_dataplane.py.diff`, готовые файлы — `new/test_01_unit.py`, `new/test_03_dataplane.py`. Дерево стенда (`multiloc-stand/work`) оставлено **равным production** (sha256 `multiloc_router.py` = `0ec2d27b…`); после согласования я внесу изменения и тесты в дерево, пересоберу манифест и прогоню целевые тесты.

<details><summary>diff тестов</summary>

```diff
--- a/tests/test_01_unit.py
+++ b/tests/test_01_unit.py
@@ -197,14 +197,15 @@
                                  "from-hiddify-s3": 20813, "from-hiddify-s4": 20814})
         for i in out["inbounds"]:
             self.assertEqual(i["sniffing"], self.base["inbounds"][0]["sniffing"])
-        # balancers: exact membership, leastPing, NO fallback (fail closed)
+        # balancers: exact membership, leastPing, fail closed (fallbackTag = the blackhole: a country never borrows another country's node)
         b = {x["tag"]: x for x in out["routing"]["balancers"]}
         self.assertEqual(sorted(b["balancer-s1"]["selector"]), ["upstream-10", "upstream-4", "upstream-6"])
         self.assertEqual(sorted(b["balancer-s2"]["selector"]), ["upstream-11", "upstream-15", "upstream-5"])
         for t in ("balancer-s1", "balancer-s2"):
             self.assertEqual(b[t]["strategy"], {"type": "leastPing"})
-            self.assertNotIn("fallbackTag", b[t])
+            self.assertEqual(b[t]["fallbackTag"], "block")
         self.assertIn("upstream-balancer", b)
+        self.assertNotIn("fallbackTag", b["upstream-balancer"])                          # the global (AUTO) balancer is not this layer's business
         # the legacy part is untouched: removing our additions gives back the original
         self.assertEqual(mr.strip(out), self.base)
 
@@ -294,6 +295,33 @@
         bad3 = copy.deepcopy(out)
         rules_of(bad3)[idx_final(rules_of(bad3)) - 1]["balancerTag"] = "balancer-nope"
         self.assertTrue(any("missing balancer" in p for p in mr.check_invariants(bad3)))
+        # a country balancer WITHOUT its fallbackTag is exactly the hole Xray's default handler opens (traffic goes to the first outbound of the file)
+        bad4 = copy.deepcopy(out)
+        del bad4["routing"]["balancers"][1]["fallbackTag"]
+        self.assertTrue(any("fallbackTag must be 'block'" in p for p in mr.check_invariants(bad4)), mr.check_invariants(bad4))
+        # the blackhole it falls back to must really exist and really be a blackhole
+        bad5 = copy.deepcopy(out)
+        bad5["outbounds"] = [o for o in bad5["outbounds"] if o.get("tag") != "block"]
+        self.assertTrue(any("not a blackhole" in p for p in mr.check_invariants(bad5)), mr.check_invariants(bad5))
+        bad6 = copy.deepcopy(out)
+        next(o for o in bad6["outbounds"] if o.get("tag") == "block")["protocol"] = "freedom"
+        self.assertTrue(any("not a blackhole" in p for p in mr.check_invariants(bad6)), mr.check_invariants(bad6))
+
+    def test_a_config_from_before_the_fix_is_brought_to_fail_closed_by_one_transform(self):
+        """what the first `multiloc_ctl.py apply` after the update sees: the LIVE config has country balancers without fallbackTag. transform() must
+        fix exactly that (nothing else), the fixed config must pass the invariants, and a second pass must change nothing."""
+        p = plan()
+        fixed = mr.transform(self.base, p)
+        old_live = copy.deepcopy(fixed)
+        for b in old_live["routing"]["balancers"]:
+            b.pop("fallbackTag", None)
+        self.assertTrue(mr.check_invariants(old_live))                                    # the old shape is now reported as a problem
+        again = mr.transform(old_live, p)
+        self.assertEqual(again, fixed)
+        self.assertEqual(mr.check_invariants(again), [])
+        self.assertEqual(mr.transform(again, p), again)                                   # idempotent: no restart for an already fixed router
+        changed = [b["tag"] for b, o in zip(again["routing"]["balancers"], old_live["routing"]["balancers"]) if b != o]
+        self.assertEqual(changed, ["balancer-s1", "balancer-s2"])                         # only the country balancers differ
 
     def test_production_sized_config_15k_rules(self):
         big = legacy_router(n_ru=15076)
```

```diff
--- a/tests/test_03_dataplane.py
+++ b/tests/test_03_dataplane.py
@@ -111,6 +111,38 @@
             systemd("start", "stand-exit-a")
         wait_until(lambda: set(via_many(PROFILES["DE XHTTP"], 3)) == {"exit-a"}, timeout=60, step=3, what="Germany recovers")
 
+    def test_dead_second_country_fails_closed_never_borrowing_the_first(self):
+        """The mirror of the test above. Germany's node is the FIRST outbound of the router file, so killing Germany was "closed" by accident (Xray's default
+        handler for a balancer without a live node picks the first outbound = the dead German node). Killing the USA is the real test: without a fallbackTag
+        the same default handler would carry the American profiles through the first outbound = out of GERMANY."""
+        systemd("stop", "stand-exit-b")
+        try:
+            # watch the whole transition (the observatory needs a couple of 3 s probe intervals to notice): not a single answer may come from exit A
+            seen = []
+            with sc.Client(PROFILES["US XHTTP"]) as c:
+                t0 = time.monotonic()
+                while time.monotonic() - t0 < 24:
+                    try:
+                        _, body = c.get(FOREIGN, timeout=5)
+                        seen.append(body.get("via") if isinstance(body, dict) else None)
+                    except Exception:
+                        seen.append(None)
+                    time.sleep(1.5)
+            self.assertNotIn("exit-a", seen, f"the American profile was carried out of Germany: {seen}")
+            self.assertEqual(seen[-3:], [None] * 3, f"USA must be closed once its exit is known to be dead: {seen}")
+            for tname in ("XHTTP", "WS", "Trojan"):
+                got = via_many(PROFILES[f"US {tname}"], 3, timeout=6)
+                self.assertEqual(got, [None] * 3, f"US {tname}: must fail closed, got {got} (exit-a here = the American traffic was borrowed by Germany)")
+            # the other country and the reserve behaviour of AUTO are unaffected
+            self.assertEqual(set(via_many(PROFILES["DE XHTTP"], 3)), {"exit-a"})
+            self.assertEqual(set(via_many(PROFILES["AUTO XHTTP"], 3)), {"exit-a"}, "AUTO keeps its reserve behaviour")
+            self.assertEqual(set(via_many(PROFILES["LEGACY xhttp"], 3)), {"exit-a"})
+            # RU destinations never needed an exit
+            self.assertEqual(via(PROFILES["US WS"], RU_DOMAIN), "direct")
+        finally:
+            systemd("start", "stand-exit-b")
+        wait_until(lambda: set(via_many(PROFILES["US XHTTP"], 3)) == {"exit-b"}, timeout=60, step=3, what="USA recovers")
+
     def test_reserve_inside_a_country_when_one_of_its_nodes_dies(self):
         # give Germany one node on exit A and one on exit B ("two servers in one country"), then kill exit A
         ctl("unassign", 2)
```
</details>

## 4. Зависимые службы

| Служба | Что происходит |
|---|---|
| `hiddify-panel` | **перезапускается один раз** инструментом `multiloc_delta_deploy.py` (иначе панель показывала бы устаревший статус `pending()`; на трафик не влияет). Недоступность панели и `/happ/` — секунды, клиенты используют кэш подписки. Можно пропустить флагом `--no-restart` (тогда статус в панели обновится при следующем перезапуске). |
| `xray-router` | **перезапускается один раз** при `multiloc_ctl.py apply` (штатный `patch_config`: инварианты → `xray -test` → атомарная замена → рестарт → ожидание входов → автооткат при неудаче). |
| `hiddify-xray`, `haproxy`, `hiddify-singbox`, БД, антишаринг, подсчёт трафика | не затрагиваются. |
| Установщик multiloc | в `STATE.json` добавляется ещё одна запись `delta_deploys` (как шесть предыдущих). |

## 5. Риск прерывания подключений

* **Перезапуск xray-router ≈ 1–3 с:** все потоки, идущие через router (вся нагрузка пользователей), обрываются, клиенты переподключаются сами (по журналу прода `Stopping → Started` укладывается в секунду, конфиг компактный). Первые соединения могут получить отказ, пока observatory не получил первый результат: **это и есть новое поведение — отказ вместо ухода в «первый outbound»**; повтор клиента через секунду проходит. Выполнять лучше в минимум нагрузки.
* **Режим «страна закрыта» теперь реален:** если observatory сочтёт мёртвыми ВСЕ узлы страны (отказ выхода либо проба `https://captive.apple.com/` недоступна через все её узлы, хотя трафик шёл бы), страна замолчит вместо молчаливой подмены. Это нужное свойство, но оно делает **мониторинг (CP-08) обязательным спутником**: падение страны должно быть видно сразу. Живые узлы DE_1 — 4 и 10 (оба в один inbound на .190), узел 6 (WG→.190) сейчас мёртв (см. CP-07), поэтому коррелированный отказ DE_1 возможен при проблеме на самом .190.
* Пользователи в режиме **AUTO** (глобальный балансировщик) **не затрагиваются** (см. §8).

## 6. Проверка до и после

* До: `scripts/check-before.sh` (только чтение) — версия файла = эталон, Xray = 26.7.28 (5ca6f4b), службы активны, `block` — blackhole, входы 20808/20811/20813 слушают, запись установщика совпадает с файлом.
* После: `scripts/check-after.sh` — файл = CP-05, `balancer-s1`/`balancer-s3` имеют `fallbackTag='block'`, `upstream-balancer` без изменений, входы слушают, 3 запроса-канарейки через входы router (DE → 203.0.113.20, NL → 203.0.113.30, AUTO → любой из двух), ошибок router за 5 минут нет.
* Поведение при отказе страны на проде **не проверяется** (выключать выход на проде нельзя); оно доказано на стенде (§2).

## 7. Откат

* **Автоматический:** внутри `patch_config` — если router не стал слушать входы за 900 с, прежний конфиг возвращается и router перезапускается; внутри `multiloc_delta_deploy.py` — при любой ошибке файлы и `STATE.json` восстанавливаются из бэкапа, панель перезапускается.
* **Ручной:** `scripts/rollback.sh <каталог бэкапа>` (каталог печатает `apply.sh`): возвращает файл слоя и `STATE.json`, перерисовывает router прежним слоем (ещё один перезапуск ≈1–3 с). Отрепетировано на стенде.
* Копия конфига router перед применением: `/root/xray-router-backups/config.json.<время>.before-multiloc.bak` (создаёт `patch_config`).

## 8. Не входит в CP-05 (решения владельца)

* **AUTO (`upstream-balancer`) остаётся без `fallbackTag`.** При «все 13 узлов мертвы» он уходит в первый outbound (сегодня `upstream-16`). Блокировка здесь имеет обратную сторону: если проба `captive.apple.com` перестанет проходить через все узлы, AUTO-пользователи потеряют сервис, хотя трафик, возможно, шёл бы. Рекомендация: сначала мониторинг (CP-08), затем вернуться к вопросу (вариант D-02: AUTO = DE-primary + явный fallback на NL-узел).
* Порядок работ: **CP-05 раньше любых починок NL-узлов (CP-06) и раньше возврата в работу WG→.190 (CP-07)**, чтобы при следующем отказе страны трафик не ушёл в чужую.

## 9. Состав пакета (`pkg/` → на RU как `/root/cp05-pkg`)

`MANIFEST.json` (1 файл), `payload/…/multiloc_router.py`, `multiloc_delta_deploy.py` и `multiloc_installer.py` (инструменты проекта), `scripts/check-before.sh`, `apply.sh`, `check-after.sh`, `rollback.sh`.
Порядок: `scp -r pkg root@RU:/root/cp05-pkg` → `bash /root/cp05-pkg/scripts/apply.sh`.
