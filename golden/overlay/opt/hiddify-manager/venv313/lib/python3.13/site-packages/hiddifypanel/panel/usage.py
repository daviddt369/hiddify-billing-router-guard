
from celery import shared_task
from sqlalchemy import func
from typing import Dict
import datetime

from hiddifypanel.drivers import user_driver
from hiddifypanel.models import *
from hiddifypanel.panel import hiddify
from hiddifypanel.database import db, db_execute, text
from hiddifypanel import cache, hutils
from hiddifypanel.accesslog import collect_recent_ips
from hiddifypanel.dev_runtime import is_local_sqlite_enabled
from loguru import logger
from hiddifypanel.panel.commercial.telegrambot.secrets import telegram_bot_token
import json
to_gig_d = 1024**3


@shared_task(ignore_result=False)
def update_local_usage():
    lock_key = "lock-update-local-usage"
    # if not cache.redis_client.set(lock_key, "locked", nx=True, ex=600):
    #     return {"msg": "last update task is not finished yet."}
    try:
        res = update_local_usage_not_lock()
        if hasattr(cache, "redis_client"):
            cache.redis_client.set(lock_key, "locked", nx=False, ex=60)

        return res
    except Exception as e:
        if hasattr(cache, "redis_client"):
            cache.redis_client.set(lock_key, "locked", nx=False, ex=60)
        logger.exception("Exception in update usage")
        raise
        return {"msg": f"Exception in update usage: {e}"}

    # return {"status": 'success', "comments":res}


def update_local_usage_not_lock():

    try:
        res = user_driver.get_users_usage(reset=True)
        return add_users_usage_new([{'uuid': uuid, "usage": uinfo['usage']} for uuid, uinfo in res.items()], child_id=0)
        # add_users_usage_uuid({"66ac79b8-8c03-4084-81c7-a2b1b3e9eefe":{"usage":1000000000}},child_id=0)
        # json_data=json.dumps([{ "uuid": uuid, "usage": uinfo["usage"]}for uuid, uinfo in {"66ac79b8-8c03-4084-81c7-a2b1b3e9eefe":{"usage":1000000000}}.items()])
        # db_execute("CALL add_usage_json(:usage_data)", usage_data= json_data, commit=True)
    except Exception as e:
        raise


def add_users_usage_uuid(uuids_bytes: Dict[str, Dict], child_id, sync=False):
    uuids_bytes = {u: v for u, v in uuids_bytes.items() if v and v.get('usage', 0) > 0}
    uuids = uuids_bytes.keys()
    users = db.session.query(User).filter(User.uuid.in_(uuids))
    dbusers_bytes = {u: uuids_bytes.get(u.uuid, {"usage": 0}) for u in users}
    _add_users_usage(dbusers_bytes, child_id, sync)  # type: ignore


def _reset_priodic_usage() -> bool:
    apply_changes = False
    last_usage_check: int = hconfig(ConfigEnum.last_priodic_usage_check) or 0
    import time
    current_time = int(time.time())
    if current_time - last_usage_check < 60 * 60 * 6:
        return apply_changes
    # reset as soon as possible in the day
    if datetime.datetime.now().hour > 5 and current_time - last_usage_check < 60 * 60 * 24:
        return apply_changes
    logger.debug("reseting user usage if needed")
    # for user in db.session.query(User).filter(User.mode != UserMode.no_reset).all():
    #     if user.user_should_reset():
    #         logger.info(f"reseting user usage for {user.uuid}")
    #         user.reset_usage(commit=False)

    today = datetime.date.today()

    db_change = False
    for user in db.session.query(User).filter(User.mode != UserMode.no_reset, User.start_date != None, User.start_date+User.package_days >= today).all():
        if user.user_should_reset():
            logger.info(f"reseting user usage for {user.uuid}")
            old_active = user.is_active
            user.reset_usage(commit=False)
            db_change = True

            if not old_active and user.is_active:
                logger.info(f"adding enabled client {user.uuid} ")
                user_driver.add_client(user)
                apply_changes = True
                send_bot_message(user)

    if db_change:
        db.session.commit()

    for user in db.session.query(User).filter(User.start_date != None, User.start_date+User.package_days < today).all():
        logger.info(f"Removing enabled client {user.uuid} ")
        if not user.is_active:
            user_driver.remove_client(user)
            apply_changes = True

    set_hconfig(ConfigEnum.last_priodic_usage_check, current_time, commit=True)
    return apply_changes


def add_users_usage_new(usages: list[dict], child_id, sync=False):
    usages = [use for use in usages if use['usage'] > 0]
    # usages[0]['usage']=1000000000000
    before_enabled_users = user_driver.get_enabled_users()

    daily_usage = {}
    cur_time=datetime.datetime.now()
    today = cur_time.date()
    db_changes = False
    for adm in db.session.query(AdminUser).all():
        daily_usage[adm.id] = db.session.query(DailyUsage).filter(DailyUsage.date == today, DailyUsage.admin_id == adm.id, DailyUsage.child_id == child_id).first()
        if daily_usage[adm.id] is None:
            logger.info(f"creating a new daily usage {today} admin={adm.id} child={child_id}")
            daily_usage[adm.id] = DailyUsage(date=today, admin_id=adm.id, child_id=child_id, usage=0)
            db.session.add(daily_usage[adm.id])
            db_changes = True
        daily_usage[adm.id].online = db.session.query(User).filter(User.added_by == adm.id).filter(func.DATE(User.last_online) == today).count()
    if db_changes:
        db.session.commit()

    apply_changes = _reset_priodic_usage()

    if is_local_sqlite_enabled():
        _apply_usages_sqlite(usages, cur_time)
    else:
        db_execute(
            "CALL add_usage_json(:usage_data,:cur_time)",
            usage_data=json.dumps(usages),
            cur_time=cur_time.strftime('%Y-%m-%d %H:%M:%S'),
            commit=True,
        )

    usage_map = {u['uuid']: u for u in usages}
    _sync_connected_devices(child_id=child_id)
    users = db.session.query(User).all()

    all_users_uuids = set()
    for user in users:
        all_users_uuids.add(user.uuid)

        user_before_active = before_enabled_users.get(user.uuid,False)
        user_active = user.is_active

        if not user_before_active and user_active:
            logger.info(f"Enabling disabled client {user.uuid} ")
            user_driver.add_client(user)
            send_bot_message(user)
            apply_changes = True
        elif user_before_active and not user_active:
            logger.info(f"Removing enabled client {user.uuid} ")
            user_driver.remove_client(user)
            send_bot_message(user)
            apply_changes = True

        if user.uuid in usage_map:
            daily_usage.get(user.added_by, daily_usage[1]).usage += usage_map[user.uuid]['usage']

    db.session.commit()

    if len(users) != len(usage_map):
        # check for zombie-users
        check_users = set(before_enabled_users.keys())-all_users_uuids
        all_db_users = {u.uuid for u in db.session.query(User).filter(User.uuid.in_(check_users)).all()}
        zombie_users = check_users-all_db_users

        for uuid in zombie_users:
            logger.info(f"Remove zombiee users {uuid} ")
            user_driver.remove_client(User(uuid=uuid))
            apply_changes = True

    if apply_changes:
        hiddify.quick_apply_users()

    return {"status": 'success', "comments": usages, "date": hutils.convert.time_to_json(cur_time)}


def _sync_connected_devices(child_id: int = 0) -> None:
    recent_ips = collect_recent_ips()
    if child_id != 0:
        return

    users = db.session.query(User).all()
    user_map = {u.uuid.lower(): u for u in users}
    details = {
        detail.user_id: detail
        for detail in db.session.query(UserDetail).filter(UserDetail.child_id == child_id).all()
    }
    changed = False

    for user in users:
        ips = recent_ips.get(user.uuid.lower(), [])
        detail = details.get(user.id)
        if detail is None:
            detail = UserDetail(user_id=user.id, child_id=child_id)
            db.session.add(detail)
            details[user.id] = detail
            changed = True

        serialized = ",".join(ips)
        if detail.connected_devices != serialized:
            detail.connected_devices = serialized
            changed = True

    for uuid, ips in recent_ips.items():
        user = user_map.get(uuid)
        if not user:
            continue
        detail = details.get(user.id)
        if detail is None:
            detail = UserDetail(user_id=user.id, child_id=child_id, connected_devices=",".join(ips))
            db.session.add(detail)
            details[user.id] = detail
            changed = True

    if changed:
        db.session.commit()


def _apply_usages_sqlite(usages: list[dict], cur_time: datetime.datetime):
    if not usages:
        return

    usage_map = {item["uuid"]: item["usage"] for item in usages if item.get("usage", 0) > 0}
    if not usage_map:
        return

    users = db.session.query(User).filter(User.uuid.in_(set(usage_map.keys()))).all()
    today = cur_time.date()
    db_changed = False

    for user in users:
        usage_bytes = int(usage_map.get(user.uuid, 0) or 0)
        if usage_bytes <= 0:
            continue

        user.current_usage = int(user.current_usage or 0) + usage_bytes
        user.last_online = cur_time
        if user.start_date is None:
            user.start_date = today
        db_changed = True

    if db_changed:
        db.session.commit()


# def _add_users_usage(users_usage_data: Dict[User, Dict], child_id, sync=False):
#     '''
#     sync: when enabled, it means we have received usages from the parent panel
#     '''
#     res = {}
#     have_change = False
#     before_enabled_users = user_driver.get_enabled_users()
#     daily_usage = {}
#     today = datetime.date.today()
#     changes = False
#     for adm in db.session.query(AdminUser).all():
#         daily_usage[adm.id] = db.session.query(DailyUsage).filter(DailyUsage.date == today, DailyUsage.admin_id == adm.id, DailyUsage.child_id == child_id).first()
#         if daily_usage[adm.id] is None:
#             logger.info(f"creating a new daily usage {today} admin={adm.id} child={child_id}")
#             daily_usage[adm.id] = DailyUsage(date=today, admin_id=adm.id, child_id=child_id)
#             db.session.add(daily_usage[adm.id])
#             changes = True
#         daily_usage[adm.id].online = db.session.query(User).filter(User.added_by == adm.id).filter(func.DATE(User.last_online) == today).count()
#     if changes:
#         db.session.commit()
#     _reset_priodic_usage()

#     # userDetails = {p.user_id: p for p in UserDetail.query.filter(UserDetail.child_id == child_id).all()}
#     for user, uinfo in users_usage_data.items():
#         usage_bytes = uinfo['usage']

#         # UserDetails things
#         # detail = UserDetail(user_id=user.id, child_id=child_id)
#         # detail = userDetails.get(user.id)
#         # if not detail:
#         #     detail = UserDetail(user_id=user.id, child_id=child_id)
#         #     db.session.add(detail)
#         # if uinfo['devices'] != detail.connected_devices:
#         #     detail.connected_devices = uinfo['devices']

#         # Enable the user if isn't already
#         if not before_enabled_users[user.uuid] and user.is_active:
#             logger.info(f"Enabling disabled client {user.uuid} ")
#             user_driver.add_client(user)
#             send_bot_message(user)
#             have_change = True

#         # Check if there's new usage value
#         if not isinstance(usage_bytes, int) or usage_bytes == 0:
#             res[user.uuid] = "No usage"
#         else:
#             # Set new daily usage of the user
#             if sync and daily_usage.get(user.added_by, daily_usage[1]).usage != usage_bytes:
#                 daily_usage.get(user.added_by, daily_usage[1]).usage = usage_bytes
#             else:
#                 daily_usage.get(user.added_by, daily_usage[1]).usage += usage_bytes

#             # Set new current usage of the user
#             if sync and user.current_usage != usage_bytes:
#                 user.current_usage = usage_bytes
#                 # detail.current_usage_GB = in_gig
#             else:
#                 user.current_usage += usage_bytes
#                 # detail.current_usage = detail.current_usage or 0
#                 # detail.current_usage += usage_bytes

#             # Change last online time of the user
#             user.last_online = datetime.datetime.now()
#             # detail.last_online = datetime.datetime.now()

#             # Set start date of user to the current datetime if it hasn't been set already
#             if user.start_date is None:
#                 user.start_date = datetime.date.today()

#             res[user.uuid] = f'{usage_bytes/1000000:0.3f}MB'

#         # Remove user from drivers(singbox, xray, wireguard etc.) if they're inactive
#         # print(before_enabled_users[user.uuid], user.is_active)
#         if before_enabled_users[user.uuid] and not user.is_active:
#             logger.info(f"Removing enabled client {user.uuid} ")

#             user_driver.remove_client(user)
#             have_change = True
#             res[user.uuid] = f"{res[user.uuid]} !OUT of USAGE! Client Removed"

#     db.session.commit()  # type: ignore

#     # Remove invalid users
#     for uuid in before_enabled_users:
#         if uuid in res:
#             continue

#         user = db.session.query(User).filter(User.uuid == uuid).first()
#         if not user:
#             user_driver.remove_client(User(uuid=uuid))
#         elif not user.is_active:
#             user_driver.remove_client(user)

#     # print("------------------", res)
#     # Apply the changes to the drivers
#     if have_change:
#         hiddify.quick_apply_users()

#     # Sync the new data with the parent node if the data has not been set by the parent node itself and the current panel is a child panel
#     if not sync and hutils.node.is_child():
#         hutils.node.child.sync_users_usage_with_parent()

#     return {"status": 'success', "comments": res, "date": hutils.convert.time_to_json(datetime.datetime.now())}


def send_bot_message(user):
    if not (telegram_bot_token() or hutils.node.is_child()):
        return
    if not user.telegram_id:
        return
    from flask_babel import lazy_gettext as _
    from hiddifypanel.panel.commercial.telegrambot import bot, Usage
    try:
        msg = Usage.get_usage_msg(user.uuid)
        msg = _("User activated!") if user.is_active else _("Package ended!") + "\n" + msg
        bot.send_message(user.telegram_id, msg, reply_markup=Usage.user_keyboard(user.uuid))
    except BaseException:
        pass
