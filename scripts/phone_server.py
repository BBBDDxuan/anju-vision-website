"""Run the two phone services together; Ctrl+C stops both. Credentials stay in local files."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import socket
import ssl
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
    parser.add_argument('--additional-address', action='append', default=[],
                        help='Another private IPv4 address owned by this PC to allow in the Host header.')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--safety-port', type=int, default=8010)
    parser.add_argument('--check', action='store_true', help='Start, check health, then stop both child services.')
    parser.add_argument('--tls-profile', type=Path, help='Windows local TLS profile directory.')
    parser.add_argument('--tls-cert')
    parser.add_argument('--tls-key')
    parser.add_argument('--tls-ca', help='CA PEM used to verify the local TLS readiness check.')
    parser.add_argument('--allow-insecure-lan', action='store_true', help='Demo credentials only on an isolated trusted LAN.')
    args = parser.parse_args()
    if args.tls_profile and any((args.tls_cert, args.tls_key, args.tls_ca, args.allow_insecure_lan)):
        parser.error('Use --tls-profile by itself, or supply individual TLS files.')
    try:
        address = private_address(args.address)
        additional_addresses = {private_address(value) for value in args.additional_address} - {address}
    except (ValueError, OSError):
        parser.error('请使用这台电脑当前可用的局域网 IPv4 地址。')
    if args.port == args.safety_port or not all(1 <= p <= 65535 for p in (args.port, args.safety_port)):
        parser.error('Choose two distinct valid ports.')
    # Refuse an already running service before changing any certificate files.
    for port, host in ((args.port, '0.0.0.0'), (args.safety_port, '127.0.0.1')):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind((host, port))
            except OSError:
                parser.error(f'端口 {port} 已被占用，请先停止旧服务，再启动手机网关。')
    local_data = os.getenv('LOCALAPPDATA')
    local_tls = args.tls_profile or (Path(local_data) / 'AnjuVision' / 'lan-tls' if local_data else None)
    if not any((args.tls_cert, args.tls_key, args.tls_ca, args.allow_insecure_lan)):
        if local_tls is None:
            parser.error('请使用 --tls-profile 指定本机 HTTPS 配置目录。')
        try:
            from scripts.lan_tls import ensure_profile
        except ImportError:
            parser.error('缺少证书生成组件，请运行 .venv\\Scripts\\python.exe -m pip install -r requirements.txt。')
        try:
            profile = ensure_profile(local_tls, {address} | additional_addresses)
        except (OSError, ValueError) as exc:
            parser.error(f'本机 HTTPS 证书自动配置失败：{exc}')
        local_tls = profile.directory
        action = {'created': '已自动创建', 'renewed': '已自动更新', 'reused': '已校验并复用'}[profile.action]
        print(f'{action}本机 HTTPS 证书，当前地址：{address}', flush=True)
        if profile.ca_changed:
            print('已创建或更换本机 CA；手机请扫描电脑新生成的二维码下载公开证书。', flush=True)
        print(f'HTTPS 配置目录：{local_tls}（私钥仅保存在电脑）', flush=True)
        args.tls_cert = str(local_tls / 'server.pem')
        args.tls_key = str(local_tls / 'server.key')
        args.tls_ca = str(local_tls / 'ca.pem')
    if bool(args.tls_cert) != bool(args.tls_key) or (args.tls_ca and not args.tls_cert):
        parser.error('Supply both TLS certificate and key; a CA is optional.')
    tls_args = ['--ssl-certfile', str(Path(args.tls_cert).resolve(strict=True)),
                '--ssl-keyfile', str(Path(args.tls_key).resolve(strict=True))] if args.tls_cert else []
    if not tls_args and not args.allow_insecure_lan:
        parser.error('找不到手机网关的 HTTPS 证书。请配置本机 TLS 文件或传入 -TlsCert 和 -TlsKey。')
    scheme = 'https' if tls_args else 'http'
    if args.allow_insecure_lan and tls_args:
        parser.error('Choose either TLS or explicit HTTP demo mode.')
    os.chdir(ROOT)
    load_dotenv(ROOT / '.env', override=False)
    phone = dotenv_values(ROOT / '.env.phone')
    token = phone.get('APP_LAN_TOKEN', '')
    if not token:
        raise ValueError('Run scripts/configure_phone.py first.')
    env = os.environ.copy()
    env['APP_LAN_TOKEN'] = token
    env['ANJU_ACCOUNTS'] = '1'
    env['ANJU_ACCOUNT_DEMO_HTTP'] = '1' if args.allow_insecure_lan else '0'
    env['ANJU_ACCOUNT_ORIGIN'] = f'{scheme}://{address}:{args.port}'
    env['ANJU_ACCOUNT_CA_PEM'] = str(Path(args.tls_ca).resolve(strict=True)) if args.tls_ca else ''
    env['ANJU_ACCOUNT_TLS_CERT'] = str(Path(args.tls_cert).resolve(strict=True)) if args.tls_cert else ''
    env['ANJU_PHONE_ADDRESS'] = address
    env['ANJU_PHONE_PORT'] = str(args.port)
    hosts = set(env.get('APP_ALLOWED_HOSTS', '127.0.0.1,localhost,[::1]').split(','))
    env['APP_ALLOWED_HOSTS'] = ','.join(sorted(hosts | {address} | additional_addresses))
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
    runtime = ROOT / 'runtime/phone-server'
    runtime.mkdir(parents=True, exist_ok=True)
    details = ROOT / 'runtime/手机连接参数.txt'
    details.write_text(
        f'安居视界手机连接参数（仅本机保存，请勿提交或公开）\n\n'
        f'统一服务地址：{scheme}://{address}:{args.port}\n统一访问令牌：{token}\n\n'
        + ''.join(f'其他已放行的本机地址：{scheme}://{item}:{args.port}\n' for item in sorted(additional_addresses)) + '\n'
        f'兼容旧 App：录像服务和安全后端均填上面的同一个地址、同一个令牌。\n'
        f'联系人角色：primary\n\n'
        f'新版 App 只需一个地址；旧版页面的两组配置填相同内容。手机与电脑须网络互通。\n'
        f'电脑网页：{scheme}://127.0.0.1:{args.port}/\n'
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
                 '--no-proxy-headers', '--no-access-log', *(tls_args if name == 'video' else [])], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT))
        deadline = time.monotonic() + 45
        # The provisioned LAN profile verifies the exact address advertised by QR.
        verify = ssl.create_default_context(cafile=args.tls_ca) if args.tls_ca else not bool(tls_args)
        ready_host = address if args.tls_ca else '127.0.0.1'
        with httpx.Client(trust_env=False, timeout=2, verify=verify) as client:
            while True:
                if any(p.poll() is not None for p in children):
                    raise RuntimeError('A service stopped. Inspect runtime/phone-server/*.log.')
                try:
                    if (client.get(f'{scheme}://{ready_host}:{args.port}/api/auth/status').status_code == 200 and
                            client.get(f'http://127.0.0.1:{args.safety_port}/health').status_code == 200):
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError('Startup timed out. Inspect runtime/phone-server/*.log.')
                time.sleep(.3)
        (runtime / 'status.json').write_text(json.dumps({
            'state': 'running', 'supervisor_pid': os.getpid(), 'pids': [p.pid for p in children],
            'address': address, 'allowed_local_addresses': sorted({address} | additional_addresses), 'ports': [args.port, args.safety_port], 'public_ports': [args.port], 'proxy_headers': False,
        }, indent=2), encoding='utf8')
        print(f'PC webpage: {scheme}://127.0.0.1:{args.port}/', flush=True)
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
