"""Synthetic local CA renewal, address changes and real loopback TLS verification."""
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import ssl
import sys
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes
import httpx
import pytest

from backend.accounts import Accounts
from scripts.lan_tls import ensure_profile
from scripts import phone_server

NOW = datetime.now(timezone.utc)
FIRST_IP = '192.168.1.9'
SECOND_IP = '10.1.1.76'


@pytest.fixture
def profile(tmp_path):
    return tmp_path / 'private-tls'


def test_first_start_creates_real_tls_and_public_pairing_payload(profile, monkeypatch, tmp_path):
    result = ensure_profile(profile, [FIRST_IP])
    assert result.action == 'created' and result.ca_changed
    ca = (profile / 'ca.pem').read_bytes()
    leaf = x509.load_pem_x509_certificate((profile / 'server.pem').read_bytes())
    monkeypatch.setenv('ANJU_ACCOUNT_CA_PEM', str(profile / 'ca.pem'))
    monkeypatch.setenv('ANJU_ACCOUNT_TLS_CERT', str(profile / 'server.pem'))
    accounts = Accounts(tmp_path / 'account.sqlite')
    assert accounts.pair_ca == ca and b'PRIVATE KEY' not in accounts.pair_ca
    assert accounts.pair_fingerprints['ca_sha256'] == hashlib.sha256(ca).hexdigest()
    assert accounts.pair_fingerprints['tls_sha256'] == leaf.fingerprint(hashes.SHA256()).hex()

    class PublicCA(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(ca)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), PublicCA)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(profile / 'server.pem', profile / 'server.key')
    server.socket = context.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        trusted = ssl.create_default_context(cafile=str(profile / 'ca.pem'))
        trusted.verify_flags |= ssl.VERIFY_X509_STRICT
        with httpx.Client(verify=trusted, trust_env=False) as client:
            response = client.get(f'https://127.0.0.1:{server.server_port}/api/auth/pair-ca')
            assert response.status_code == 200 and response.content == ca
        with socket.create_connection(('127.0.0.1', server.server_port)) as raw:
            with trusted.wrap_socket(raw, server_hostname=FIRST_IP) as secured:
                assert hashlib.sha256(secured.getpeercert(binary_form=True)).hexdigest() == \
                    accounts.pair_fingerprints['tls_sha256']
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_same_ip_reuses_certificate_without_rewriting_keys(profile):
    ensure_profile(profile, [FIRST_IP], now=NOW)
    files = {name: ((profile / name).read_bytes(), (profile / name).stat().st_mtime_ns)
             for name in ('ca.key', 'ca.pem', 'server.key', 'server.pem')}
    result = ensure_profile(profile, [FIRST_IP], now=NOW)
    assert result.action == 'reused' and not result.ca_changed
    for name, original in files.items():
        assert ((profile / name).read_bytes(), (profile / name).stat().st_mtime_ns) == original


def test_changed_ip_renews_leaf_but_keeps_ca_and_archives_old_leaf(profile):
    ensure_profile(profile, [FIRST_IP], now=NOW)
    old_ca, old_server = (profile / 'ca.pem').read_bytes(), (profile / 'server.pem').read_bytes()
    result = ensure_profile(profile, [SECOND_IP], now=NOW)
    assert result.action == 'renewed' and not result.ca_changed
    assert (profile / 'ca.pem').read_bytes() == old_ca
    assert (profile / 'server.pem').read_bytes() != old_server
    assert next((profile / 'backups').glob('*/server.pem')).read_bytes() == old_server
    assert ensure_profile(profile, [FIRST_IP], now=NOW).action == 'reused'


def test_expiring_leaf_renews_without_changing_trusted_ca(profile):
    ensure_profile(profile, [FIRST_IP], now=NOW)
    original = (profile / 'ca.pem').read_bytes()
    result = ensure_profile(profile, [FIRST_IP], now=NOW + timedelta(days=340))
    assert result.action == 'renewed' and not result.ca_changed
    assert (profile / 'ca.pem').read_bytes() == original


def test_metadata_cannot_claim_certificate_covers_another_ip(profile):
    ensure_profile(profile, [FIRST_IP], now=NOW)
    original = (profile / 'server.pem').read_bytes()
    (profile / 'address.txt').write_text(SECOND_IP, encoding='ascii')
    assert ensure_profile(profile, [SECOND_IP], now=NOW).action == 'renewed'
    assert (profile / 'server.pem').read_bytes() != original


def test_old_profile_without_ca_key_is_reused_until_it_needs_reissuing(profile):
    ensure_profile(profile, [FIRST_IP], now=NOW)
    (profile / 'ca.key').unlink()
    original = (profile / 'ca.pem').read_bytes()
    assert ensure_profile(profile, [FIRST_IP], now=NOW).action == 'reused'
    result = ensure_profile(profile, [SECOND_IP], now=NOW)
    assert result.ca_changed and result.action == 'renewed'
    assert (profile / 'ca.pem').read_bytes() != original
    assert next((profile / 'backups').glob('*/ca.pem')).read_bytes() == original


def test_missing_or_corrupt_leaf_is_repaired_without_changing_ca(profile):
    ensure_profile(profile, [FIRST_IP], now=NOW)
    original = (profile / 'ca.pem').read_bytes()
    (profile / 'server.pem').write_text('incomplete certificate', encoding='ascii')
    assert not ensure_profile(profile, [FIRST_IP], now=NOW).ca_changed
    (profile / 'server.key').unlink()
    assert not ensure_profile(profile, [FIRST_IP], now=NOW).ca_changed
    assert (profile / 'ca.pem').read_bytes() == original


@pytest.mark.parametrize('address', ['8.8.8.8', '127.0.0.1', '::1', 'not-an-ip'])
def test_invalid_lan_address_does_not_create_profile(profile, address):
    with pytest.raises(ValueError):
        ensure_profile(profile, [address])
    assert not profile.exists()


def test_symlink_profile_does_not_modify_target(tmp_path):
    target = tmp_path / 'real'
    target.mkdir()
    link = tmp_path / 'link'
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip('Creating symlinks requires platform permission')
    with pytest.raises(ValueError, match='路径'):
        ensure_profile(link, [FIRST_IP])
    assert not list(target.iterdir())


class _FreePort:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def setsockopt(self, *args):
        pass

    def bind(self, *args):
        pass


def _gateway_until_settings(monkeypatch, tmp_path, arguments):
    from backend.config import Settings

    class SettingsReached(Exception):
        pass

    def stop_before_services():
        raise SettingsReached

    monkeypatch.setattr(phone_server, 'ROOT', tmp_path)
    monkeypatch.setattr(phone_server, 'private_address', lambda address: address)
    monkeypatch.setattr(phone_server.socket, 'socket', lambda: _FreePort())
    monkeypatch.setattr(Settings, 'from_env', stop_before_services)
    monkeypatch.setattr(sys, 'argv', ['phone_server.py', '--address', FIRST_IP, *arguments])
    (tmp_path / '.env.phone').write_text('APP_LAN_TOKEN=' + 'test-only-' + 'x' * 32, encoding='ascii')
    with pytest.raises(SettingsReached):
        phone_server.main()


def test_gateway_creates_a_missing_tls_profile_before_launching_services(monkeypatch, tmp_path, profile):
    _gateway_until_settings(monkeypatch, tmp_path, ['--tls-profile', str(profile)])
    assert (profile / 'server.pem').is_file()
    assert (profile / 'ca.key').is_file()


def test_gateway_leaves_explicit_tls_files_unmodified(monkeypatch, tmp_path, profile):
    import scripts.lan_tls as tls
    ensure_profile(profile, [FIRST_IP])
    original = (profile / 'server.pem').read_bytes()

    def forbidden(*args):
        pytest.fail('Explicit TLS must not be auto-managed')

    monkeypatch.setattr(tls, 'ensure_profile', forbidden)
    _gateway_until_settings(monkeypatch, tmp_path, [
        '--tls-cert', str(profile / 'server.pem'), '--tls-key', str(profile / 'server.key'),
        '--tls-ca', str(profile / 'ca.pem'),
    ])
    assert (profile / 'server.pem').read_bytes() == original


def test_busy_gateway_port_does_not_create_or_rotate_certificates(monkeypatch, profile):
    class BusyPort(_FreePort):
        def bind(self, *args):
            raise OSError('port in use')

    monkeypatch.setattr(phone_server, 'private_address', lambda address: address)
    monkeypatch.setattr(phone_server.socket, 'socket', lambda: BusyPort())
    monkeypatch.setattr(sys, 'argv', [
        'phone_server.py', '--address', FIRST_IP, '--tls-profile', str(profile),
    ])
    with pytest.raises(SystemExit) as stopped:
        phone_server.main()
    assert stopped.value.code == 2
    assert not profile.exists()
