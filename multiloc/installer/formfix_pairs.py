"""The routing-form fix (owner approved 2026-10-13, after the incident of 2026-10-13 04:40): exact replacement pairs, shared by build_payload.py (the stand payload) and the production delta.

Why: the form «Маршрутизация» held ALL ~15k rules in one textarea, could not be saved because the rule sources store single hosts as `ip:x.x.x.x/32` (stock `normalize_ip_rule("ip", ...)` refuses a
network), and Save deleted EVERY rule row and re-created the rows from the text - rules imported from the rule sources lost their source link (and policy) and a failed Save invited deleting
the big source. Now: `ip:` takes a single host written as /32 or /128; the text shows ONLY the manual rules (enabled, direct); Save changes them in place (unchanged rows keep id / order / comment),
never touches the source rules or anything the text cannot show, skips a line that equals a source rule; at most 10 error lines are shown."""
PKG = "opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel"

FORMFIX = {
    f"{PKG}/hutils/commercial_routing.py": [
        ('''    v = value.strip().lower()
    if rule_type == "ip":
        return str(ipaddress.ip_address(v))
    if rule_type == "cidr":''', '''    v = value.strip().lower()
    if rule_type == "ip":
        if "/" in v:                                     # one host written as a network (x.x.x.x/32, ::1/128): how the rule sources store them and how the text printed them back
            net = ipaddress.ip_network(v, strict=False)
            if net.prefixlen != net.max_prefixlen:
                raise ValueError("'ip:' takes one address (a single host may be written /32); a network needs the 'cidr:' prefix")
            return str(net.network_address)
        return str(ipaddress.ip_address(v))
    if rule_type == "cidr":'''),
        ('''def load_enabled_custom_rules() -> list[dict[str, Any]]:
    return [''', '''def load_enabled_custom_rules(manual_only: bool = False) -> list[dict[str, Any]]:
    """the enabled rules in id order; manual_only = only the rules added by hand (not imported from a rule source) - what the routing form's text shows"""
    query = CommercialRoutingCustomRule.query.filter_by(enabled=True)
    if manual_only:
        query = query.filter(CommercialRoutingCustomRule.source_id.is_(None))
    return ['''),
        ('''        for r in CommercialRoutingCustomRule.query.filter_by(enabled=True).order_by(CommercialRoutingCustomRule.id.asc()).all()
    ]''', '''        for r in query.order_by(CommercialRoutingCustomRule.id.asc()).all()
    ]'''),
    ],
    f"{PKG}/panel/admin/BusinessAdmin.py": [
        ('''form.custom_ru_rules_bulk.data = commercial_routing.custom_rules_to_bulk_text(commercial_routing.load_enabled_custom_rules())''',
         '''form.custom_ru_rules_bulk.data = commercial_routing.custom_rules_to_bulk_text(commercial_routing.load_enabled_custom_rules(manual_only=True))'''),
        ('''            if errors:
                for err in errors:
                    hutils.flask.flash(f"Ошибка в строке {err.line_no} списка правил: {err.error}", "danger")
                return self._render_settings(form, commercial_routing_notice=None, test_result=None)

            unique_rules = {}
            for rule in rules:
                unique_rules[(rule["rule_type"], rule["normalized_value"])] = rule

            CommercialRoutingCustomRule.query.delete()
            for rule in unique_rules.values():
                db.session.add(CommercialRoutingCustomRule(**rule))

            db.session.commit()
''', '''            if errors:
                for err in errors[:10]:
                    hutils.flask.flash(f"Ошибка в строке {err.line_no} списка правил: {err.error}", "danger")
                if len(errors) > 10:
                    hutils.flask.flash(f"…и ещё {len(errors) - 10} ошибок (показаны первые 10). Ничего не сохранено.", "danger")
                return self._render_settings(form, commercial_routing_notice=None, test_result=None)

            unique_rules = {}
            for rule in rules:
                unique_rules[(rule["rule_type"], rule["normalized_value"])] = rule

            # The text holds ONLY the manually added rules (enabled, "direct"). The rules imported from the rule sources - and anything else the text cannot show - are never touched.
            # Changed in place: an unchanged rule keeps its id / order / comment, a rule the text no longer has is deleted, a new one is added, a line that equals a source rule is skipped.
            from sqlalchemy import and_
            shown = (CommercialRoutingCustomRule.source_id.is_(None), CommercialRoutingCustomRule.enabled.is_(True), CommercialRoutingCustomRule.outbound_policy == "direct_ru")
            mine = {(r.rule_type, r.normalized_value): r for r in CommercialRoutingCustomRule.query.filter(*shown).all()}
            others = {(r.rule_type, r.normalized_value) for r in CommercialRoutingCustomRule.query.filter(~and_(*shown)).all()}
            removed = added = skipped = 0
            for key, row in mine.items():
                if key not in unique_rules:
                    db.session.delete(row)
                    removed += 1
            for key, rule in unique_rules.items():
                if key in mine:
                    continue
                if key in others:
                    skipped += 1
                    continue
                db.session.add(CommercialRoutingCustomRule(**rule))
                added += 1
            db.session.commit()
            hutils.flask.flash(f"Свои правила сохранены: добавлено {added}, убрано {removed}, без изменений {len(mine) - removed}"
                               + (f"; {skipped} уже есть в источниках - пропущено" if skipped else "") + f". Правила из источников и прочие ({len(others)}) не затронуты.", "success")
'''),
    ],
}
