"""Run the two phone services together; Ctrl+C stops both. Credentials stay in local files."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
from dotenv import dotenv_values, load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def private_address(value):
    address = ipaddress.IPv4Address(value)
    if not any(address in ipaddress.IPv4Network(network) for network in
               ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
        raise ValueError('Use this computer\'s private LAN IPv4 address.')
    # Fail early if the supplied address no longer belongs to this computer.
    with socket.socket() as probe:
        probe.bind((str(address), 0))
    return str(address)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--address', required=True)
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--safety-port', type=int, default=8010)
    parser.add_argument('--check', action='store_true', help='Start, check health, then stop both child services.')
    args = parser.parse_args()
    address = private_address(args.address)
    if args.port == args.safety_port or not all(1 <= p <= 65535 for p in (args.port, args.safety_port)):
        parser.error('Choose two distinct valid ports.')
    os.chdir(ROOT)
    load_dotenv(ROOT / '.env', override=False)
    phone = dotenv_values(ROOT / '.env.phone')
    token = phone.get('APP_LAN_TOKEN', '')
    if not token:
        raise ValueError('Run scripts/configure_phone.py first.')
    env = os.environ.copy()
    env['APP_LAN_TOKEN'] = token
    hosts = set(env.get('APP_ALLOWED_HOSTS', '127.0.0.1,localhost,[::1]').split(','))
    env['APP_ALLOWED_HOSTS'] = ','.join(sorted(hosts | {address}))
    env['PYTHONUTF8'] = '1'
    env['APP_SAFETY_INTERNAL_URL'] = f'http://127.0.0.1:{args.safety_port}'
    env['PYTHONPATH'] = os.pathsep.join(filter(None, [str(ROOT / 'elder-safety/backend'), str(ROOT), env.get('PYTHONPATH')]))
    # Validate settings before opening listeners; never silently switch cloud mode.
    from backend.config import Settings
    from dataclasses import replace
    replace(Settings.from_env(), lan_token=token)
    sys.path.insert(0, str(ROOT / 'elder-safety/backend'))
    from safety_backend.config import Settings as SafetySettings
    SafetySettings.from_env()
    for port, host in ((args.port, '0.0.0.0'), (args.safety_port, '127.0.0.1')):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind((host, port))
    runtime = ROOT / 'runtime/phone-server'
    runtime.mkdir(parents=True, exist_ok=True)
    details = ROOT / 'runtime/手机连接参数.txt'
    details.write_text(
        f'安居视界手机连接参数（仅本机保存，请勿提交或公开）\n\n'
        f'统一服务地址：http://{address}:{args.port}\n统一访问令牌：{token}\n\n'
        f'兼容旧 App：录像服务和安全后端均填上面的同一个地址、同一个令牌。\n'
        f'联系人角色：primary\n\n'
        f'新版 App 只需一个地址；旧版页面的两组配置填相同内容。手机与电脑须网络互通。\n'
        f'电脑网页：http://127.0.0.1:{args.port}/\n'
        f'{args.safety_port} 仅在电脑内部监听，不向手机开放。\n'
        '统一告警只纳入确认的视觉候选。通知适配器未接入时不会声称后台推送已送达。\n'
        '连接状态：GET /api/connection；统一告警：GET /api/alerts。\n', encoding='utf8')
    children, logs = [], []
    try:
        for name, target, port, host in [('safety', 'backend.phone_app:create_internal_safety_app', args.safety_port, '127.0.0.1'),
                                         ('video', 'backend.phone_app:create_app', args.port, '0.0.0.0')]:
            log = (runtime / (name + '.log')).open('w', encoding='utf8')
            logs.append(log)
            children.append(subprocess.Popen(
                [sys.executable, '-m', 'uvicorn', target, '--factory', '--host', host, '--port', str(port),
                 '--no-proxy-headers', '--no-access-log'], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT))
        deadline = time.monotonic() + 45
        with httpx.Client(trust_env=False, timeout=2) as client:
            while True:
                if any(p.poll() is not None for p in children):
                    raise RuntimeError('A service stopped. Inspect runtime/phone-server/*.log.')
                try:
                    if (client.get(f'http://127.0.0.1:{args.port}/api/health').status_code == 200 and
                            client.get(f'http://127.0.0.1:{args.safety_port}/health').status_code == 200):
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError('Startup timed out. Inspect runtime/phone-server/*.log.')
                time.sleep(.3)
        (runtime / 'status.json').write_text(json.dumps({
            'state': 'running', 'supervisor_pid': os.getpid(), 'pids': [p.pid for p in children],
            'address': address, 'ports': [args.port, args.safety_port], 'public_ports': [args.port], 'proxy_headers': False,
        }, indent=2), encoding='utf8')
        print(f'PC webpage: http://127.0.0.1:{args.port}/', flush=True)
        print(f'Phone connection settings: {details}', flush=True)
        print('Both services are running. Keep this terminal open; Ctrl+C stops both.', flush=True)
        if args.check:
            return
        while True:
            if any(p.poll() is not None for p in children):
                raise RuntimeError('A service stopped. Inspect runtime/phone-server/*.log.')
            time.sleep(.5)
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for log in logs:
            log.close()
        (runtime / 'status.json').write_text(json.dumps({'state': 'stopped', 'ports': [args.port, args.safety_port]}), encoding='utf8')


if __name__ == '__main__':
    main()
