#!/opt/hiddify-manager/.venv313/bin/python
from urllib.parse import urlparse
import click
import os
from strenum import StrEnum
import subprocess
import re

HIDDIFY_DIR = '/opt/hiddify-manager/'


class Command(StrEnum):
    '''The value of each command refers to the command shell file'''
    apply = os.path.join(HIDDIFY_DIR, 'apply_configs.sh')
    install = os.path.join(HIDDIFY_DIR, 'install.sh')
    # reinstall = os.path.join(HIDDIFY_DIR,'reinstall.sh')
    update = os.path.join(HIDDIFY_DIR, 'update.sh')
    status = os.path.join(HIDDIFY_DIR, 'status.sh')
    restart_services = os.path.join(HIDDIFY_DIR, 'restart.sh')
    temporary_short_link = os.path.join(HIDDIFY_DIR, 'nginx/add2shortlink.sh')
    temporary_access = os.path.join(
        HIDDIFY_DIR, 'hiddify-panel/temporary_access.sh')
    update_usage = os.path.join(HIDDIFY_DIR, 'hiddify-panel/update_usage.sh')
    get_cert = os.path.join(HIDDIFY_DIR, 'acme.sh/get_cert.sh')
    # apply-users command is actually "install.sh apply_users"
    apply_users = os.path.join(HIDDIFY_DIR, 'install.sh')
    id = 'id'


def run(cmd: list[str]):
    subprocess.run(cmd, shell=False, check=True)


@click.group(chain=True)
def cli():
    pass


@cli.command('id')
def id():
    out = subprocess.check_output(['id'])
    print(out.decode())


@cli.command('apply')
def apply():
    cmd = [Command.apply.value, '--no-gui']
    run(cmd)


@cli.command('install')
def install():
    cmd = [Command.install.value, '--no-gui']
    run(cmd)


# @cli.command('reinstall')
# def reinstall():
#     cmd = [Command.reinstall.value]
#     run(cmd)


@cli.command('update')
def update():
    cmd = [Command.update.value, '--no-gui']
    run(cmd)


@cli.command('restart-services')
def restart_services():
    cmd = [Command.restart_services.value, '--no-gui']
    run(cmd)


@cli.command('status')
def status():
    cmd = [Command.status.value, '--no-gui']
    run(cmd)


def add_temporary_short_link_assert_input(url: str, slug: str) -> None:
    '''Returns None if everything is valid otherwise returns an error'''

    assert url, f"Error: Invalid value for '--url' / '-u': \"\" is not a valid url"

    assert urlparse(url), f"Error: Invalid value for '--url' / '-u': {url} is an invalid url"

    assert slug, f"Error: Invalid value for '-slug' / '-s': \"\" is not a valid slug"

    assert slug.isalnum(), f"Error: Invalid value for '-slug' / '-s': \"\" is not a alphanumeric"

    assert is_valid_url(url), f"Error: Invalid character in url: {url}"

    # don't need to sanitize slug but we do for good (we are not lucky)
    assert is_valid_slug(slug), f"Error: Invalid character in slug: {slug}"


def is_valid_url(url) -> bool:
    if not urlparse(url):
        return False

    pattern = r"^[a-zA-Z0-9:/@.-]+$"
    return bool(re.match(pattern, url))


def is_valid_slug(slug) -> bool:
    pattern = r"^[a-zA-Z0-9\-]+$"
    return bool(re.match(pattern, slug))


@cli.command('temporary-short-link')
@click.option('--url', '-u', type=str, help='The url that is going to be short', required=True)
@click.option('--slug', '-s', type=str, help='The secret code', required=True)
@click.option('--period', '-p', type=int, help='The time period that link remains active', required=False)
def add_temporary_short_link(url: str, slug: str, period: int):
    # validate inputs
    add_temporary_short_link_assert_input(url, slug)

    cmd = [Command.temporary_short_link.value, url, slug, str(period)]

    run(cmd)


# @cli.command('temporary-access')
# @click.option('--port', '-p', type=int, help='The port that is going to be open', required=True)
# def add_temporary_access(port: int):
#     cmd = [Command.temporary_access.value, str(port)]
#     run(cmd)


def is_domain_valid(d):
    pattern = r"^[a-zA-Z0-9\-.]+$"
    return bool(re.match(pattern, d))


@cli.command('get-cert')
@click.option('--domain', '-d', type=str, help='The domain that needs certificate', required=True)
def get_cert(domain: str):
    if not domain:
        return
    assert is_domain_valid(domain), f"Error: Invalid domain passed to the get_cert command: {domain}"
    cmd = [Command.get_cert.value, domain]
    run(cmd)


@cli.command('update-usage')
def update_usage():
    cmd = [Command.update_usage.value]
    run(cmd)


@cli.command('apply-users')
def apply_users():
    cmd = [Command.apply_users.value, 'apply_users', '--no-gui']
    run(cmd)


@cli.command('update-wg-usage')
def update_wg_usage():
    wg_raw_output = subprocess.check_output(['wg', 'show', 'hiddifywg', 'transfer'])
    print(wg_raw_output.decode())



@cli.command('commercial-routing-apply')
def commercial_routing_apply():
    from hiddifypanel import create_app

    service_path = '/etc/systemd/system/xray-router.service'
    service_body = """[Unit]
Description=Local Xray router-core for commercial routing
After=network.target nss-lookup.target

[Service]
User=root
ExecStart=/usr/bin/xray run -config /etc/xray-router/config.json
Restart=always
RestartSec=3
LimitNOFILE=infinity

[Install]
WantedBy=multi-user.target
"""

    os.makedirs('/etc/xray-router', exist_ok=True)
    if not os.path.exists(service_path):
        with open(service_path, 'w', encoding='utf-8') as fh:
            fh.write(service_body)
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', 'enable', 'xray-router'], check=False)

    os.environ.setdefault('HIDDIFY_CFG_PATH', '/opt/hiddify-manager/hiddify-panel/app.cfg')
    os.environ.setdefault('HIDDIFY_CONFIG_PATH', '/opt/hiddify-manager/')
    os.chdir('/opt/hiddify-manager/hiddify-panel')

    app = create_app(app_mode='cli')
    with app.app_context():
        from hiddifypanel.hutils.commercial_routing import apply_router_core_config
        result = apply_router_core_config()
    subprocess.run(['systemctl', 'enable', 'xray-router'], check=False)
    print(
        f"Applied {result.target_path} and restarted {result.service_name} custom_rules={result.custom_rules_total}"
    )


# ROUTING_INSTALL_BEGIN --- do not remove this line
@cli.command('commercial-routing-apply')
def commercial_routing_apply():
    from hiddifypanel import create_app

    service_path = '/etc/systemd/system/xray-router.service'
    service_body = """[Unit]
Description=Local Xray router-core for commercial routing
After=network.target nss-lookup.target

[Service]
User=root
ExecStart=/usr/bin/xray run -config /etc/xray-router/config.json
Restart=always
RestartSec=3
LimitNOFILE=infinity

[Install]
WantedBy=multi-user.target
"""

    os.makedirs('/etc/xray-router', exist_ok=True)
    if not os.path.exists(service_path):
        with open(service_path, 'w', encoding='utf-8') as fh:
            fh.write(service_body)
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', 'enable', 'xray-router'], check=False)

    os.environ.setdefault('HIDDIFY_CFG_PATH', '/opt/hiddify-manager/hiddify-panel/app.cfg')
    os.environ.setdefault('HIDDIFY_CONFIG_PATH', '/opt/hiddify-manager/')
    os.chdir('/opt/hiddify-manager/hiddify-panel')

    app = create_app(app_mode='cli')
    with app.app_context():
        from hiddifypanel.hutils.commercial_routing import apply_router_core_config
        result = apply_router_core_config()
    subprocess.run(['systemctl', 'enable', 'xray-router'], check=False)
    print(
        f"Applied {result.target_path} and restarted {result.service_name} custom_rules={result.custom_rules_total}"
    )
# ROUTING_INSTALL_END --- do not remove this line


# MULTILOC_ROUTER_APPLY_BEGIN --- the panel's apply buttons start the router apply in the BACKGROUND (scripts/multiloc_router_apply.py); do not remove this line
@cli.command('router-apply-start')
@click.option('--mode', type=click.Choice(['auto', 'countries', 'full', 'accept', 'discard']), required=True)
def router_apply_start(mode):
    # fixed command, fixed script, a whitelisted mode: prints one JSON line; exit 0 = started, 3 = another apply is running, 2 = could not start
    raise SystemExit(subprocess.run([os.path.join(HIDDIFY_DIR, '.venv313/bin/python'), os.path.join(HIDDIFY_DIR, 'scripts/multiloc_router_apply.py'),
                                     'start', '--mode', mode]).returncode)
# MULTILOC_ROUTER_APPLY_END --- do not remove this line

if __name__ == "__main__":
    cli()
