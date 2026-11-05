"""Verify release APKs with existing Android tools and atomically publish locally.

No build, upload, source archive, keystore access, or credential creation occurs.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app_updates import (  # noqa: E402
    ABIS, DIGEST, MAX_APK_BYTES, MAX_MANIFEST_BYTES, MAX_NOTES_CHARS, MAX_VERSION_CODE, PACKAGE_NAME, InvalidUpdate,
    fingerprint, open_private, private_path, read_manifest, validate_manifest, verified_apk,
)


# Trusted original release pin; follows App build-c.ps1 ExpectedSigningSha256 policy.
DEFAULT_RELEASE_CERT_SHA256 = '2472d43f63dbea0d2d3c34933114caf3dc532055c97ceaa40e96a968070b3e35'


def normalize_certificate(value):
    value = value.replace(':', '').lower().strip()
    if not DIGEST.fullmatch(value):
        raise InvalidUpdate('Expected a SHA-256 certificate fingerprint (64 hex digits)')
    return value


def run_tool(arguments):
    try:
        result = subprocess.run([str(arg) for arg in arguments], capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InvalidUpdate(f'Could not run Android verification tool: {Path(arguments[0]).name}') from exc
    if result.returncode:
        # Do not echo arbitrary tool diagnostics or environment values.
        raise InvalidUpdate(f'Android verification failed: {Path(arguments[0]).name}')
    return result.stdout


def android_tools(sdk=None, java=None, *, aapt=None, apksigner=None):
    if aapt or apksigner:
        if not aapt or not apksigner:
            raise InvalidUpdate('Pass both --aapt and --apksigner for explicit tool paths')
        aapt, signer = Path(aapt), Path(apksigner)
        if not aapt.is_file() or not signer.is_file():
            raise InvalidUpdate('Explicit Android tool paths must be existing files')
    else:
        sdk = sdk or os.getenv('ANDROID_SDK_ROOT') or os.getenv('ANDROID_HOME')
        if not sdk:
            raise InvalidUpdate('Pass --sdk, or both --aapt and --apksigner with existing tool paths')
        candidates = []
        for directory in (Path(sdk) / 'build-tools').glob('*'):
            if re.fullmatch(r'\d+(?:\.\d+){1,3}', directory.name):
                candidates.append((tuple(map(int, directory.name.split('.'))), directory))
        for _, directory in sorted(candidates, reverse=True):
            aapt = directory / ('aapt2.exe' if os.name == 'nt' else 'aapt2')
            signer = directory / 'lib' / 'apksigner.jar'
            if aapt.is_file() and signer.is_file():
                break
        else:
            raise InvalidUpdate('SDK needs existing build-tools with aapt2 and lib/apksigner.jar')
    java = java or (str(Path(os.environ['JAVA_HOME']) / 'bin' / ('java.exe' if os.name == 'nt' else 'java'))
                    if os.getenv('JAVA_HOME') else shutil.which('java'))
    if signer.suffix.lower() == '.jar' and (not java or not Path(java).is_file()):
        raise InvalidUpdate('Pass --java with the existing Java executable (or set JAVA_HOME)')
    return aapt, signer, Path(java) if java else None


def inspect_apk(apk, tools):
    aapt, signer, java = tools
    badging = run_tool([aapt, 'dump', 'badging', apk])
    package_line = next((line for line in badging.splitlines() if line.startswith('package: ')), '')
    fields = dict(re.findall(r"([A-Za-z]+)='([^']*)'", package_line))
    if fields.get('name') != PACKAGE_NAME or not fields.get('versionName'):
        raise InvalidUpdate('APK must have the production package and a version name')
    if 'application-debuggable' in badging:
        raise InvalidUpdate('Debuggable APKs cannot be published')
    # Read the actual binary manifest; combine unsigned low/major fields into longVersionCode.
    tree = run_tool([aapt, 'dump', 'xmltree', apk, '--file', 'AndroidManifest.xml'])

    def integer_attribute(name, required=False):
        lines = [line for line in tree.splitlines() if re.search(r'\bA: android:' + name + r'\(', line)]
        if not lines and not required:
            return 0
        if len(lines) != 1:
            raise InvalidUpdate(f'Missing or ambiguous Android {name}')
        match = re.search(r'=\(type 0x1[01]\)0x([0-9a-fA-F]+)\b', lines[0])
        if not match:
            raise InvalidUpdate(f'Android {name} must be an integer literal')
        value = int(match[1], 16)
        if not 0 <= value <= 0xffffffff:
            raise InvalidUpdate(f'Invalid Android {name}')
        return value

    low = integer_attribute('versionCode', required=True)
    major = integer_attribute('versionCodeMajor')
    code = (major << 32) | low
    if not 1 <= code <= MAX_VERSION_CODE or fields.get('versionCode') != str(low):
        raise InvalidUpdate('APK version code is invalid or inconsistent')
    with zipfile.ZipFile(apk) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise InvalidUpdate('Duplicate APK ZIP entries')
        native_abis = {name.split('/')[1] for name in names
                       if re.fullmatch(r'lib/[^/]+/[^/]+\.so', name)}
    if not native_abis or not native_abis.issubset(ABIS):
        raise InvalidUpdate('APK must contain supported native ABI libraries')
    native_line = next((line for line in badging.splitlines() if line.startswith('native-code:')), '')
    if set(re.findall(r"'([^']+)'", native_line)) != native_abis:
        raise InvalidUpdate('APK native ABI metadata is inconsistent')
    signer_command = [java, '-jar', signer] if signer.suffix.lower() == '.jar' else [signer]
    signatures = run_tool([*signer_command, 'verify', '--verbose', '--print-certs', apk])
    certs = re.findall(r'^Signer #(\d+) certificate SHA-256 digest: ([0-9a-fA-F]{64})\s*$', signatures, re.M)
    if len(certs) != 1 or certs[0][0] != '1':
        raise InvalidUpdate('APK must have exactly one verified current signer')
    if re.search(r'^Signer #1 certificate DN:.*CN\s*=\s*Android Debug\b', signatures, re.M | re.I):
        raise InvalidUpdate('Android debug certificates cannot be published')
    return {'version_name': fields['versionName'], 'version_code': code,
            'abis': sorted(native_abis), 'certificate_sha256': certs[0][1].lower()}


def ensure_directory(root):
    root = Path(os.path.abspath(root))
    for directory in (*reversed(root.parents), root):
        try:
            directory.lstat()
        except FileNotFoundError:
            directory.mkdir()
        private_path(directory)
        if not directory.is_dir():
            raise InvalidUpdate('Update root must be a private directory')
    return root


@contextmanager
def publishing_lock(root):
    lock = root / '.publish.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise InvalidUpdate('Another publisher is running, or .publish.lock needs manual review') from exc
    try:
        os.write(fd, str(os.getpid()).encode('ascii'))
        yield
    finally:
        os.close(fd)
        lock.unlink()


def publish(apks, notes, *, root, tools, expected_certificate=None):
    if not isinstance(notes, str) or len(notes) > MAX_NOTES_CHARS:
        raise InvalidUpdate('Notes must contain at most 10000 characters')
    if not apks:
        raise InvalidUpdate('At least one exact APK path is required')
    root = ensure_directory(root)
    with publishing_lock(root), tempfile.TemporaryDirectory(prefix='.publish-', dir=root) as temporary:
        old = read_manifest(root)
        pinned = old['certificate_sha256'] if old else None
        expected = normalize_certificate(expected_certificate) if expected_certificate else (pinned or DEFAULT_RELEASE_CERT_SHA256)
        if pinned and expected != pinned:
            raise InvalidUpdate('Configured certificate differs from the already published certificate')
        staged = []
        updates = []
        used_abis = set()
        version_names = set()
        for index, source in enumerate(apks):
            source = Path(source)
            if source.suffix.lower() != '.apk':
                raise InvalidUpdate('--apk must name an exact .apk file')
            destination = Path(temporary) / f'{index}.apk'
            with open_private(source) as incoming, destination.open('xb') as output:
                before = os.fstat(incoming.fileno())
                if not 1 <= before.st_size <= MAX_APK_BYTES:
                    raise InvalidUpdate('APK must be nonempty and at most 256 MiB')
                copied = 0
                while block := incoming.read(1024 * 1024):
                    copied += len(block)
                    if copied > MAX_APK_BYTES:
                        raise InvalidUpdate('APK grew beyond the 256 MiB limit')
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
                if fingerprint(before) != fingerprint(os.fstat(incoming.fileno())):
                    raise InvalidUpdate('Source APK changed while copying')
            size = destination.stat().st_size
            with destination.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            info = inspect_apk(destination, tools)
            if info['certificate_sha256'] != expected:
                raise InvalidUpdate('APK release certificate does not match the trusted fingerprint')
            version_names.add(info['version_name'])
            for abi in info['abis']:
                if abi in used_abis:
                    raise InvalidUpdate('Only one input APK per ABI is allowed')
                used_abis.add(abi)
                updates.append({'version_name': info['version_name'], 'version_code': info['version_code'],
                                'abi': abi, 'package_name': PACKAGE_NAME, 'size_bytes': size, 'sha256': digest,
                                'download_path': '/api/app/apk/' + digest, 'notes': notes})
            staged.append((destination, digest))
        if len(version_names) != 1:
            raise InvalidUpdate('All APKs in one publication must have the same version name')
        combined = list(old['updates']) if old else []
        for item in updates:
            previous = [entry for entry in combined if entry['abi'] == item['abi']]
            same = next((entry for entry in previous if entry['version_code'] == item['version_code']), None)
            if same and any(same[key] != item[key] for key in ('sha256', 'size_bytes', 'version_name')):
                raise InvalidUpdate('Cannot replace an already published ABI/version with different content')
            if previous and item['version_code'] < max(entry['version_code'] for entry in previous):
                raise InvalidUpdate('Publication would downgrade an ABI')
            if same:
                combined.remove(same)
            combined.append(item)
        document = validate_manifest({'schema_version': 1, 'certificate_sha256': expected, 'updates': combined})
        encoded = (json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        if len(encoded) > MAX_MANIFEST_BYTES:
            raise InvalidUpdate('Manifest history is full; review retention before publishing')
        # All inputs are now verified. Install content without ever replacing an existing digest.
        for source, digest in staged:
            target = root / (digest + '.apk')
            try:
                if os.name == 'nt':
                    # Windows rename is atomic and refuses an existing destination;
                    # unlike hard links, it also works on this machine's G: filesystem.
                    os.rename(source, target)
                else:
                    os.link(source, target)
            except FileExistsError:
                pass
            item = next(entry for entry in updates if entry['sha256'] == digest)
            with verified_apk(root, item):
                pass
        # Validate retained history too, before committing a new catalog.
        for item in combined:
            with verified_apk(root, item):
                pass
        manifest = Path(temporary) / 'manifest.json'
        with manifest.open('xb') as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        private_path(root)
        if (root / 'manifest.json').exists() or (root / 'manifest.json').is_symlink():
            private_path(root / 'manifest.json')
        os.replace(manifest, root / 'manifest.json')
        return updates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apk', type=Path, action='append', required=True, help='Exact local release APK; repeat per ABI.')
    notes = parser.add_mutually_exclusive_group(required=True)
    notes.add_argument('--notes', help='Release notes (up to 10000 characters).')
    notes.add_argument('--notes-file', type=Path, help='UTF-8 release notes file.')
    parser.add_argument('--sdk', type=Path, help='Existing Android SDK, containing build-tools/.')
    parser.add_argument('--aapt', type=Path, help='Exact existing aapt2 executable; use with --apksigner.')
    parser.add_argument('--apksigner', type=Path, help='Exact existing apksigner.jar or apksigner executable/batch file.')
    parser.add_argument('--java', type=Path, help='Existing JDK java executable.')
    parser.add_argument('--expected-cert-sha256', default=os.getenv('APP_UPDATE_CERT_SHA256'),
                        help='Trusted release certificate SHA-256; defaults to the published pin or original release fingerprint.')
    args = parser.parse_args()
    try:
        text = args.notes if args.notes is not None else args.notes_file.read_text(encoding='utf-8-sig')
        updates = publish(args.apk, text, root=ROOT / 'runtime' / 'app-updates',
                          tools=android_tools(args.sdk, args.java, aapt=args.aapt, apksigner=args.apksigner),
                          expected_certificate=args.expected_cert_sha256)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        parser.exit(1, f'Publication refused: {exc}\n')
    print(json.dumps({'published': updates, 'manifest': str(ROOT / 'runtime/app-updates/manifest.json')},
                     ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
