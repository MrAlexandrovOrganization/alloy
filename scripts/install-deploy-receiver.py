#!/usr/bin/env python3
"""Explicit administrative bootstrap; JSON stdin contains public keys and receiver source."""
from pathlib import Path
import base64, json, os, pwd, datetime
if os.geteuid() != 0:
    raise SystemExit('Receiver bootstrap requires root')
payload = json.load(__import__('sys').stdin)
config = payload['config']
if config['host'] not in {'server', 'hostkey-russia', 'hostkey-finland', 'private-test-server', 'wsl'}:
    raise SystemExit('Unknown deploy host')
expected = {'alloy', 'prometheus', 'loki', 'grafana'} if config['host'] == 'wsl' else {'alloy'}
if set(config['projects']) != expected or type(config['sudo_make']) is not bool:
    raise SystemExit('Unexpected host project policy')
import re
for project, key in payload['keys'].items():
    if project not in {'alloy', 'prometheus', 'loki', 'grafana'} or not re.fullmatch('ssh-ed25519 [A-Za-z0-9+/=]+', key):
        raise SystemExit('Invalid repository public key')
user = pwd.getpwnam('maxim')
install = Path('/usr/local/lib/observability-deploy')
install.mkdir(parents=True, exist_ok=True)
install.chmod(493)
receiver = install / 'receiver.py'
receiver.write_bytes(base64.b64decode(payload['receiver']))
receiver.chmod(493)
config_file = Path('/etc/observability-deploy.json')
config_file.write_text(json.dumps(payload['config']) + '\n')
config_file.chmod(420)
home = Path(user.pw_dir)
for relative in ('.ssh', '.local', '.local/state', '.local/state/observability-deploy'):
    p = home / relative
    if not p.exists():
        p.mkdir(mode=448)
        os.chown(p, user.pw_uid, user.pw_gid)
auth = home / '.ssh/authorized_keys'
old = auth.read_text() if auth.exists() else ''
backup = home / '.local/state/observability-deploy' / ('authorized_keys.before-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
backup.write_text(old)
backup.chmod(384)
os.chown(backup, user.pw_uid, user.pw_gid)
lines = old.splitlines()
for project, key in payload['keys'].items():
    marker = 'observability-cd-' + project + '-20261015'
    lines = [line for line in lines if not line.endswith(' ' + marker)]
    if project not in expected:
        continue
    options = 'restrict'
    command = '/usr/bin/python3 /usr/local/lib/observability-deploy/receiver.py ' + project
    lines.append(options + ',command="' + command + '" ' + key + ' ' + marker)
temporary = auth.with_name('authorized_keys.observability-new')
temporary.write_text('\n'.join(lines) + '\n')
temporary.chmod(384)
os.chown(temporary, user.pw_uid, user.pw_gid)
temporary.replace(auth)
print('Installed restricted deploy access on ' + payload['config']['host'])
if config['host'] == 'hostkey-russia':
    name = 'observability-deploy'
    import subprocess
    try:
        gateway = pwd.getpwnam(name)
    except KeyError:
        subprocess.run(['useradd', '--system', '--create-home', '--shell', '/bin/sh', name], check=True)
        gateway = pwd.getpwnam(name)
    gateway_home = Path(gateway.pw_dir)
    ssh = gateway_home / '.ssh'
    ssh.mkdir(exist_ok=True, mode=448)
    os.chown(ssh, gateway.pw_uid, gateway.pw_gid)
    gateway_lines = []
    for project, key in payload['keys'].items():
        addresses = ['100.64.0.1', '100.64.0.3', '100.64.0.4', '100.64.0.5', '100.64.0.6'] if project == 'alloy' else ['100.64.0.3']
        options = 'restrict,port-forwarding,' + ','.join(('permitopen="' + address + ':22"' for address in addresses))
        gateway_lines.append(options + ',command="/bin/false" ' + key + ' observability-cd-' + project + '-20261015')
    gateway_auth = ssh / 'authorized_keys'
    old_gateway = gateway_auth.read_text() if gateway_auth.exists() else ''
    gateway_lines = [line for line in old_gateway.splitlines() if ' observability-cd-' not in line] + gateway_lines
    gateway_auth.write_text('\n'.join(gateway_lines) + '\n')
    gateway_auth.chmod(384)
    os.chown(gateway_auth, gateway.pw_uid, gateway.pw_gid)
    dropin = Path('/etc/ssh/sshd_config.d/99-observability-deploy.conf')
    previous = dropin.read_bytes() if dropin.exists() else None
    dropin.write_text('Match User observability-deploy\n    AllowTcpForwarding local\n    PermitListen none\n    PasswordAuthentication no\n    KbdInteractiveAuthentication no\n    ForceCommand /bin/false\nMatch all\n')
    try:
        subprocess.run(['/usr/sbin/sshd', '-t'], check=True)
    except subprocess.CalledProcessError:
        if previous is None:
            dropin.unlink()
        else:
            dropin.write_bytes(previous)
        raise
    subprocess.run(['systemctl', 'reload', 'ssh'], check=True)
    print('Dedicated gateway permits only local TCP forwarding to pinned SSH destinations')
