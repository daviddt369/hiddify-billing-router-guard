import json
import datetime

import wtforms as wtf
from flask import redirect, render_template, request
from flask_babel import lazy_gettext as _
from flask_classful import FlaskView
from flask_wtf import FlaskForm

from hiddifypanel import hutils
from hiddifypanel.auth import login_required
from hiddifypanel.database import db
from hiddifypanel.models import Role, User
from hiddifypanel.antishare.config import AntiShareSettings
from hiddifypanel.antishare.models import AntiShareConfig, AntiShareEvent, AntiShareIPProfile, AntiShareState, AntiShareUserOverride
from hiddifypanel.antishare.nftables import NftBanBackend
from hiddifypanel.antishare.runner import ensure_tables


class AntiShareSettingsForm(FlaskForm):
    enabled = wtf.BooleanField("Anti-share enabled")
    window_seconds = wtf.IntegerField("IP window seconds", validators=[wtf.validators.NumberRange(min=30, max=3600)])
    learning_days = wtf.IntegerField("Learning days", validators=[wtf.validators.NumberRange(min=1, max=30)])
    retention_days = wtf.IntegerField("Retention days", validators=[wtf.validators.NumberRange(min=7, max=180)])
    trusted_recent_days = wtf.IntegerField("Trusted recent days", validators=[wtf.validators.NumberRange(min=1, max=30)])
    trust_decay_per_day = wtf.FloatField("Trust decay per day", validators=[wtf.validators.NumberRange(min=0.0, max=5.0)])
    score_decay_clean = wtf.FloatField("Score decay on clean cycle", validators=[wtf.validators.NumberRange(min=0.0, max=5.0)])
    score_plus1 = wtf.FloatField("Score bump for +1 IP", validators=[wtf.validators.NumberRange(min=0.0, max=5.0)])
    score_plus2 = wtf.FloatField("Score bump for +2 IP", validators=[wtf.validators.NumberRange(min=0.0, max=5.0)])
    score_plus3 = wtf.FloatField("Score bump for +3+ IP", validators=[wtf.validators.NumberRange(min=0.0, max=10.0)])
    suspect_score = wtf.FloatField("Suspect threshold", validators=[wtf.validators.NumberRange(min=0.0, max=10.0)])
    warn_score = wtf.FloatField("Warn threshold", validators=[wtf.validators.NumberRange(min=0.0, max=10.0)])
    block_score = wtf.FloatField("Block threshold", validators=[wtf.validators.NumberRange(min=0.0, max=10.0)])
    severe_new_ip_threshold = wtf.IntegerField("Severe new IP threshold", validators=[wtf.validators.NumberRange(min=1, max=50)])
    severe_traffic_ratio = wtf.FloatField("Severe traffic ratio", validators=[wtf.validators.NumberRange(min=1.0, max=100.0)])
    ban_seconds = wtf.IntegerField("Ban seconds", validators=[wtf.validators.NumberRange(min=60, max=604800)])
    telegram_enabled = wtf.BooleanField("Telegram notifications enabled")
    nft_enabled = wtf.BooleanField("NFT enforcement enabled")
    nft_dry_run = wtf.BooleanField("NFT dry-run")
    scan_limit = wtf.IntegerField("User scan limit", validators=[wtf.validators.NumberRange(min=1, max=100000)])
    submit = wtf.SubmitField(_("Сохранить"))


class AntiShareAdmin(FlaskView):
    route_base = "/anti-share-admin"
    decorators = [login_required(roles={Role.super_admin})]

    @staticmethod
    def _profile_view(p: AntiShareIPProfile, now: datetime.datetime) -> dict:
        ban_until = p.last_ban_until
        is_banned = bool(ban_until and ban_until > now)
        is_trusted = bool(p.is_trusted)
        if is_banned:
            badge_class = "label-danger"
            state_label = "в бане"
        elif is_trusted:
            badge_class = "label-success"
            state_label = "доверенный"
        else:
            badge_class = "label-default"
            state_label = "обычный"
        return {
            "ip": p.ip,
            "is_trusted": is_trusted,
            "is_banned": is_banned,
            "trust_score": round(float(p.trust_score or 0.0), 2),
            "last_seen_at": p.last_seen_at,
            "last_banned_at": p.last_banned_at,
            "last_ban_until": ban_until,
            "badge_class": badge_class,
            "state_label": state_label,
            "can_trust": not is_trusted,
            "can_untrust": is_trusted,
            "can_unban": is_banned,
        }

    def _config_row(self) -> AntiShareConfig:
        ensure_tables()
        row = AntiShareConfig.query.order_by(AntiShareConfig.id.asc()).first()
        if row:
            return row
        settings = AntiShareSettings.from_env()
        row = AntiShareConfig(
            enabled=settings.enabled,
            window_seconds=settings.window_seconds,
            learning_days=settings.learning_days,
            retention_days=settings.retention_days,
            trusted_recent_days=settings.trusted_recent_days,
            trust_decay_per_day=settings.trust_decay_per_day,
            score_decay_clean=settings.score_decay_clean,
            score_plus1=settings.score_plus1,
            score_plus2=settings.score_plus2,
            score_plus3=settings.score_plus3,
            suspect_score=settings.suspect_score,
            warn_score=settings.warn_score,
            block_score=settings.block_score,
            severe_new_ip_threshold=settings.severe_new_ip_threshold,
            severe_traffic_ratio=settings.severe_traffic_ratio,
            ban_seconds=settings.ban_seconds,
            telegram_enabled=settings.telegram_enabled,
            nft_enabled=settings.nft_enabled,
            nft_dry_run=settings.nft_dry_run,
            nft_helper=settings.nft_helper,
            scan_limit=settings.scan_limit,
            current_ip_snapshot_limit=settings.current_ip_snapshot_limit,
            service_name=settings.service_name,
        )
        db.session.add(row)
        db.session.commit()
        return row

    def _build_form(self) -> AntiShareSettingsForm:
        cfg = self._config_row()
        return AntiShareSettingsForm(
            enabled=cfg.enabled,
            window_seconds=cfg.window_seconds,
            learning_days=cfg.learning_days,
            retention_days=cfg.retention_days,
            trusted_recent_days=cfg.trusted_recent_days,
            trust_decay_per_day=cfg.trust_decay_per_day,
            score_decay_clean=cfg.score_decay_clean,
            score_plus1=cfg.score_plus1,
            score_plus2=cfg.score_plus2,
            score_plus3=cfg.score_plus3,
            suspect_score=cfg.suspect_score,
            warn_score=cfg.warn_score,
            block_score=cfg.block_score,
            severe_new_ip_threshold=cfg.severe_new_ip_threshold,
            severe_traffic_ratio=cfg.severe_traffic_ratio,
            ban_seconds=cfg.ban_seconds,
            telegram_enabled=cfg.telegram_enabled,
            nft_enabled=cfg.nft_enabled,
            nft_dry_run=cfg.nft_dry_run,
            scan_limit=cfg.scan_limit,
        )

    def _summary(self) -> dict:
        state_rows = AntiShareState.query.all()
        now = datetime.datetime.utcnow()
        since_7d = now - datetime.timedelta(days=7)

        warn_count = AntiShareEvent.query.filter(
            AntiShareEvent.event_type == "state_transition",
            AntiShareEvent.state_after == "warned",
            AntiShareEvent.created_at >= since_7d,
        ).count()
        block_count = AntiShareEvent.query.filter(
            AntiShareEvent.event_type == "state_transition",
            AntiShareEvent.state_after == "blocked",
            AntiShareEvent.created_at >= since_7d,
        ).count()

        states = {"learning": 0, "normal": 0, "suspect": 0, "warned": 0, "blocked": 0}
        for row in state_rows:
            states[row.state] = states.get(row.state, 0) + 1

        return {
            "users_tracked": len(state_rows),
            "warn_count_7d": warn_count,
            "block_count_7d": block_count,
            "states": states,
        }

    def _override_row(self, user_id: int) -> AntiShareUserOverride:
        row = AntiShareUserOverride.query.filter(AntiShareUserOverride.user_id == user_id).first()
        if row:
            return row
        row = AntiShareUserOverride(user_id=user_id, disabled=False)
        db.session.add(row)
        db.session.flush()
        return row

    def _user_profiles(self, user_id: int) -> list[dict]:
        now = datetime.datetime.utcnow()
        profiles = (
            AntiShareIPProfile.query.filter(AntiShareIPProfile.user_id == user_id)
            .order_by(AntiShareIPProfile.last_seen_at.desc(), AntiShareIPProfile.trust_score.desc())
            .limit(12)
            .all()
        )
        return [self._profile_view(p, now) for p in profiles]

    def _user_rows(self) -> list[dict]:
        now = datetime.datetime.utcnow()
        since_7d = now - datetime.timedelta(days=7)
        since_3d = now - datetime.timedelta(days=3)
        cycle_events = AntiShareEvent.query.filter(
            AntiShareEvent.event_type == "cycle_summary",
            AntiShareEvent.created_at >= since_7d,
        ).order_by(AntiShareEvent.created_at.desc()).all()

        metrics: dict[int, dict] = {}
        for event in cycle_events:
            payload = {}
            try:
                payload = json.loads(event.payload or "{}")
            except Exception:
                payload = {}
            item = metrics.setdefault(event.user_id, {"vals_7d": [], "vals_3d": []})
            current_ip_count = int(payload.get("current_ip_count") or 0)
            item["vals_7d"].append(current_ip_count)
            if event.created_at >= since_3d:
                item["vals_3d"].append(current_ip_count)

        rows = []
        for user in User.query.order_by(User.id.asc()).all():
            state = AntiShareState.query.filter(AntiShareState.user_id == user.id).first()
            if not state:
                continue
            warn_count = AntiShareEvent.query.filter(
                AntiShareEvent.user_id == user.id,
                AntiShareEvent.event_type == "state_transition",
                AntiShareEvent.state_after == "warned",
            ).count()
            block_count = AntiShareEvent.query.filter(
                AntiShareEvent.user_id == user.id,
                AntiShareEvent.event_type == "state_transition",
                AntiShareEvent.state_after == "blocked",
            ).count()
            m = metrics.get(user.id, {"vals_7d": [], "vals_3d": []})
            vals_7d = m["vals_7d"]
            vals_3d = m["vals_3d"]
            profiles = self._user_profiles(user.id)
            profile_by_ip = {item["ip"]: item for item in profiles}
            current_ips = []
            for ip in json.loads(state.last_ips_snapshot or "[]"):
                current_ips.append(
                    profile_by_ip.get(
                        ip,
                        {
                            "ip": ip,
                            "is_trusted": False,
                            "is_banned": False,
                            "badge_class": "label-default",
                            "state_label": "обычный",
                            "last_ban_until": None,
                        },
                    )
                )
            rows.append(
                {
                    "user_id": user.id,
                    "name": user.name,
                    "telegram_id": getattr(user, "telegram_id", None),
                    "max_ips": user.max_ips,
                    "state": state.state,
                    "score": float(state.score or 0.0),
                    "current_ip_count": int(state.current_ip_count or 0),
                    "allowed_ip_count": int(state.allowed_ip_count or 0),
                    "current_ips": current_ips,
                    "warn_count": warn_count,
                    "block_count": block_count,
                    "avg_ips_3d": round(sum(vals_3d) / len(vals_3d), 2) if vals_3d else 0.0,
                    "avg_ips_7d": round(sum(vals_7d) / len(vals_7d), 2) if vals_7d else 0.0,
                    "ban_until": state.ban_until,
                    "learning_until": state.learning_until,
                    "override_disabled": bool(
                        getattr(
                            AntiShareUserOverride.query.filter(AntiShareUserOverride.user_id == user.id).first(),
                            "disabled",
                            False,
                        )
                    ),
                    "profiles": profiles,
                }
            )
        return rows

    def _handle_user_action(self) -> None:
        action = (request.form.get("action") or "").strip().lower()
        user_id = int(request.form.get("target_user_id") or 0)
        target_ip = (request.form.get("target_ip") or "").strip()
        if not action or not user_id:
            hutils.flask.flash("Anti-share action payload is incomplete.", "danger")
            return

        user = User.query.filter(User.id == user_id).first()
        if not user:
            hutils.flask.flash("User not found for anti-share action.", "danger")
            return

        state = AntiShareState.query.filter(AntiShareState.user_id == user_id).first()
        settings = AntiShareSettings.load()
        backend = NftBanBackend(
            helper_path=settings.nft_helper,
            enabled=settings.nft_enabled,
            dry_run=settings.nft_dry_run,
        )

        if action == "reset_score":
            if state:
                state.score = 0.0
                state.state = "normal"
                state.ban_until = None
                state.violation_started_at = None
            db.session.add(
                AntiShareEvent(
                    user_id=user_id,
                    event_type="manual_reset_score",
                    score_before=float(getattr(state, "score", 0.0) or 0.0),
                    score_after=0.0,
                    state_before=str(getattr(state, "state", "") or ""),
                    state_after="normal",
                    payload=json.dumps({"source": "admin_ui"}, ensure_ascii=False),
                )
            )
            db.session.commit()
            hutils.flask.flash(f"Anti-share score reset for user {user.name}.", "success")
            return

        if action == "toggle_disable":
            row = self._override_row(user_id)
            row.disabled = not bool(row.disabled)
            db.session.commit()
            hutils.flask.flash(
                f"Anti-share {'disabled' if row.disabled else 'enabled'} for user {user.name}.",
                "success",
            )
            return

        profile = AntiShareIPProfile.query.filter(
            AntiShareIPProfile.user_id == user_id,
            AntiShareIPProfile.ip == target_ip,
        ).first()
        if not profile:
            hutils.flask.flash("IP profile not found for anti-share action.", "danger")
            return

        if action == "trust_ip":
            profile.is_trusted = True
            profile.trust_score = max(float(profile.trust_score or 0.0), 3.0)
            db.session.commit()
            hutils.flask.flash(f"IP {target_ip} marked as trusted.", "success")
            return

        if action == "untrust_ip":
            profile.is_trusted = False
            profile.trust_score = min(float(profile.trust_score or 0.0), 0.25)
            db.session.commit()
            hutils.flask.flash(f"IP {target_ip} trust removed.", "success")
            return

        if action == "unban_ip":
            try:
                backend.unban_ip(target_ip)
            except Exception:
                hutils.flask.flash(f"Failed to unban IP {target_ip}.", "danger")
                return
            profile.last_ban_until = None
            if state and state.ban_until:
                state.ban_until = None
            db.session.commit()
            hutils.flask.flash(f"IP {target_ip} unbanned.", "success")
            return

        hutils.flask.flash(f"Unknown anti-share action: {action}", "danger")

    def index(self):
        ensure_tables()
        form = self._build_form()
        return render_template(
            "anti-share-settings.html",
            form=form,
            summary=self._summary(),
            user_rows=self._user_rows(),
        )

    def post(self):
        ensure_tables()
        action = (request.form.get("action") or "").strip().lower()
        if action:
            self._handle_user_action()
            return redirect(request.path)

        form = AntiShareSettingsForm()
        if not form.validate_on_submit():
            hutils.flask.flash(_("config.validation-error"), "danger")
            return render_template(
                "anti-share-settings.html",
                form=form,
                summary=self._summary(),
                user_rows=self._user_rows(),
            )

        cfg = self._config_row()
        cfg.enabled = bool(form.enabled.data)
        cfg.window_seconds = int(form.window_seconds.data or 120)
        cfg.learning_days = int(form.learning_days.data or 7)
        cfg.retention_days = int(form.retention_days.data or 45)
        cfg.trusted_recent_days = int(form.trusted_recent_days.data or 7)
        cfg.trust_decay_per_day = float(form.trust_decay_per_day.data or 0.15)
        cfg.score_decay_clean = float(form.score_decay_clean.data or 0.25)
        cfg.score_plus1 = float(form.score_plus1.data or 0.25)
        cfg.score_plus2 = float(form.score_plus2.data or 0.5)
        cfg.score_plus3 = float(form.score_plus3.data or 1.0)
        cfg.suspect_score = float(form.suspect_score.data or 0.5)
        cfg.warn_score = float(form.warn_score.data or 0.75)
        cfg.block_score = float(form.block_score.data or 1.0)
        cfg.severe_new_ip_threshold = int(form.severe_new_ip_threshold.data or 3)
        cfg.severe_traffic_ratio = float(form.severe_traffic_ratio.data or 5.0)
        cfg.ban_seconds = int(form.ban_seconds.data or 3600)
        cfg.telegram_enabled = bool(form.telegram_enabled.data)
        cfg.nft_enabled = bool(form.nft_enabled.data)
        cfg.nft_dry_run = bool(form.nft_dry_run.data)
        cfg.scan_limit = int(form.scan_limit.data or 1000)
        db.session.commit()
        hutils.flask.flash("Anti-share settings saved.", "success")
        form = self._build_form()
        return render_template(
            "anti-share-settings.html",
            form=form,
            summary=self._summary(),
            user_rows=self._user_rows(),
        )
