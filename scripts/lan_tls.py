"""Maintain a private per-computer CA and LAN HTTPS profile before startup.

Only ca.pem and server.pem are public. Never serve this directory or its backups.
Explicit externally supplied TLS files are outside this module's ownership.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import ipaddress
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

PROFILE_FILES = ('address.txt', 'server.pem', 'server.key', 'ca.pem', 'ca.key')
RENEW_BEFORE = timedelta(days=30)
LEAF_LIFETIME = timedelta(days=365)
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(n) for n in
                         ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


@dataclass(frozen=True)
class PreparedTLS:
    directory: Path
    action: str
    ca_changed: bool = False


def default_profile():
    base = os.getenv('LOCALAPPDATA')
    if not base:
        raise ValueError('请通过 --tls-profile 指定本机 HTTPS 配置目录。')
    return Path(base) / 'AnjuVision' / 'lan-tls'


def _no_links(path):
    for entry in (*reversed(path.parents), path):
        try:
            info = entry.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('HTTPS 配置路径不能包含符号链接或重解析点。')


def _private_directory(path):
    _no_links(path)
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if os.name == 'nt':
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        identity = subprocess.run(['whoami.exe', '/user', '/fo', 'csv', '/nh'],
                                  capture_output=True, check=True, creationflags=flags)
        sid = re.search(rb'S-1-\d+(?:-\d+)+', identity.stdout)
        if not sid:
            raise ValueError('无法确定本机用户，不能安全保存 HTTPS 私钥。')
        result = subprocess.run(['icacls.exe', str(path), '/inheritance:r', '/grant:r',
                                 '*' + sid[0].decode('ascii') + ':(OI)(CI)F'],
                                capture_output=True, creationflags=flags)
        if result.returncode:
            raise ValueError('无法限制 HTTPS 配置目录权限，请检查该目录的所有者。')
    else:
        path.chmod(0o700)


@contextmanager
def _locked_profile(path):
    lock = path / '.configuration.lock'
    _no_links(lock)
    with os.fdopen(os.open(lock, os.O_CREAT | os.O_RDWR, 0o600), 'r+b') as stream:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _write(path, data):
    _no_links(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.tls-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _backup(path):
    existing = [path / name for name in PROFILE_FILES if (path / name).exists()]
    if not existing:
        return
    directory = path / 'backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    _private_directory(directory)
    for source in existing:
        _no_links(source)
        shutil.copyfile(source, directory / source.name)
        if os.name != 'nt':
            (directory / source.name).chmod(0o600)


def _public_key(key):
    return key.public_key().public_bytes(serialization.Encoding.DER,
                                        serialization.PublicFormat.SubjectPublicKeyInfo)


def _read_key(path, certificate):
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if _public_key(key) != certificate.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo):
        raise ValueError('Certificate and private key do not match')
    return key


def _key_bytes(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def _valid_ca(ca, now):
    ca.verify_directly_issued_by(ca)
    return (ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
            and ca.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign
            and ca.not_valid_before_utc <= now
            and ca.not_valid_after_utc > now + LEAF_LIFETIME + RENEW_BEFORE)


def _valid_server(path, ca, addresses, now):
    server = x509.load_pem_x509_certificate((path / 'server.pem').read_bytes())
    server.verify_directly_issued_by(ca)
    _read_key(path / 'server.key', server)
    names = server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return (not server.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
            and ExtendedKeyUsageOID.SERVER_AUTH in
            server.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
            and server.not_valid_before_utc <= now
            and server.not_valid_after_utc > now + RENEW_BEFORE
            and addresses.issubset(set(names.get_values_for_type(x509.IPAddress)))
            and 'localhost' in names.get_values_for_type(x509.DNSName))


def ensure_profile(directory, addresses, *, now=None):
    """Reuse identity on renewal; archive old files before any identity change."""
    directory = Path(os.path.abspath(directory))
    addresses = {ipaddress.ip_address(value) for value in addresses}
    if not addresses or any(not isinstance(a, ipaddress.IPv4Address) or
                            not any(a in network for network in PRIVATE_NETWORKS)
                            for a in addresses):
        raise ValueError('证书地址必须是电脑的局域网 IPv4 地址。')
    addresses |= {ipaddress.ip_address('127.0.0.1'), ipaddress.ip_address('::1')}
    now = now or datetime.now(timezone.utc)
    _private_directory(directory)
    with _locked_profile(directory):
        for name in PROFILE_FILES:
            _no_links(directory / name)
        ca, ca_key = None, None
        invalid = (FileNotFoundError, ValueError, TypeError, InvalidSignature, x509.ExtensionNotFound)
        try:
            ca = x509.load_pem_x509_certificate((directory / 'ca.pem').read_bytes())
            if not _valid_ca(ca, now):
                ca = None
        except invalid:
            ca = None
        if ca is not None:
            try:
                if _valid_server(directory, ca, addresses, now):
                    # Metadata alone is never used to decide whether an IP is covered.
                    advertised = '\n'.join(sorted(map(str, addresses))) + '\n'
                    metadata = directory / 'address.txt'
                    if not metadata.exists() or metadata.read_text(encoding='ascii') != advertised:
                        _write(metadata, advertised.encode('ascii'))
                    return PreparedTLS(directory, 'reused')
            except invalid:
                pass
            try:
                ca_key = _read_key(directory / 'ca.key', ca)
            except invalid:
                pass
        _backup(directory)
        ca_changed = ca is None or ca_key is None
        if ca_changed:
            ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Anju Vision Local CA')])
            ca = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                  .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
                  .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=3650))
                  .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                  .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                      key_encipherment=False, data_encipherment=False, key_agreement=False,
                      key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
                  .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
                  .sign(ca_key, hashes.SHA256()))
        # Keep prior private addresses when issuing a new leaf, so returning to
        # a previously used Wi-Fi doesn't require another certificate change.
        try:
            previous = x509.load_pem_x509_certificate((directory / 'server.pem').read_bytes())
            old_ips = previous.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            addresses |= {a for a in old_ips.get_values_for_type(x509.IPAddress)
                          if isinstance(a, ipaddress.IPv4Address) and any(a in n for n in PRIVATE_NETWORKS)}
        except invalid:
            pass
        if len(addresses) > 64:
            raise ValueError('本机证书记录的地址过多，请检查 HTTPS 配置。')
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        server = (x509.CertificateBuilder()
                  .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Anju Vision LAN')]))
                  .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
                  .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + LEAF_LIFETIME)
                  .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                  .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),
                      *(x509.IPAddress(a) for a in sorted(addresses, key=str))]), critical=False)
                  .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                  .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                      key_encipherment=True, data_encipherment=False, key_agreement=False,
                      key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
                  .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                  .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
                  .sign(ca_key, hashes.SHA256()))
        ca_pem = ca.public_bytes(serialization.Encoding.PEM)
        if ca_changed:
            _write(directory / 'ca.key', _key_bytes(ca_key))
            _write(directory / 'ca.pem', ca_pem)
        _write(directory / 'server.key', _key_bytes(key))
        _write(directory / 'server.pem', server.public_bytes(serialization.Encoding.PEM) + ca_pem)
        _write(directory / 'address.txt', ('\n'.join(sorted(map(str, addresses))) + '\n').encode('ascii'))
        return PreparedTLS(directory, 'renewed' if (directory / 'backups').exists() else 'created', ca_changed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--address', action='append', required=True)
    args = parser.parse_args()
    result = ensure_profile(args.profile or default_profile(), args.address)
    print(f'HTTPS profile: {result.directory}; {result.action}; CA changed: {result.ca_changed}')


if __name__ == '__main__':
    main()
