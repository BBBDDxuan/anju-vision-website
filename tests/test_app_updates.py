"""Synthetic APK bytes/tool output only; no build, real signer, camera or model call."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import zipfile

from fastapi.testclient import TestClient
import pytest

from backend import phone_app
from backend.app_updates import MAX_APK_BYTES, MAX_NOTES_CHARS, MAX_VERSION_CODE, PACKAGE_NAME, InvalidUpdate, read_manifest
from backend.main import create_app as video_app
from scripts import publish_app_update as publisher
from tests.conftest import MockVisionProvider
from tests.test_alert_center import safety  # noqa: F401

TOKEN = 'synthetic-updater-' + 'u' * 32
HEADERS = {'Authorization': 'Bearer ' + TOKEN}
CERT = 'a' * 64


def entry(content=b'synthetic APK, not installable', code=4008, abi='arm64-v8a'):
    sha = hashlib.sha256(content).hexdigest()
    return dict(version_name='1.2.3', version_code=code, abi=abi, package_name=PACKAGE_NAME,
                size_bytes=len(content), sha256=sha, download_path='/api/app/apk/' + sha, notes='合成测试')


def write_catalog(root, entries=None, content=b'synthetic APK, not installable'):
    root.mkdir(parents=True, exist_ok=True)
    entries = entries or [entry(content)]
    (root / 'manifest.json').write_text(json.dumps(dict(schema_version=1, certificate_sha256=CERT, updates=entries)),
                                      encoding='utf8')
    (root / (entries[0]['sha256'] + '.apk')).write_bytes(content)
    return entries[0]


@pytest.fixture
def phone(settings, safety, tmp_path, monkeypatch):
    monkeypatch.setattr(phone_app, 'ROOT', tmp_path)
    app = phone_app.create_app(replace(settings, lan_token=TOKEN), MockVisionProvider(), safety_repo=safety)
    with TestClient(app, base_url='http://localhost', client=('192.168.1.20', 50000)) as client:
        yield client, tmp_path / 'runtime/app-updates'


def version(client, code=0, abi='arm64-v8a', headers=HEADERS):
    return client.get('/api/app/version', params={'version_code': code, 'abi': abi}, headers=headers)


def test_empty_publication_and_required_query(phone):
    client, root = phone
    response = version(client)
    assert response.status_code == 200 and response.json() == {'available': False, 'update': None}
    assert client.get('/api/app/apk/' + 'a' * 64, headers=HEADERS).status_code == 404
    assert client.get('/api/app/version', headers=HEADERS).status_code == 422
    assert not root.exists()


@pytest.mark.parametrize('code,abi,available', [(0, 'arm64-v8a', True), (4007, 'arm64-v8a', True),
    (4008, 'arm64-v8a', False), (9000, 'arm64-v8a', False), (0, 'x86_64', False), (0, 'armeabi-v7a', False)])
def test_actual_version_and_exact_abi(phone, code, abi, available):
    client, root = phone
    item = write_catalog(root)
    response = version(client, code, abi)
    assert response.status_code == 200
    assert response.json() == {'available': available, 'update': item if available else None}


def test_selects_highest_version_not_manifest_order_and_handles_long_code(phone):
    client, root = phone
    items = [entry(code=2**32 + 4008), entry(code=4007), entry(abi='x86_64', code=9000)]
    write_catalog(root, [items[0]])
    # Separate hashes represent distinct APK versions.
    for index in (1, 2):
        content = f'synthetic {index}'.encode()
        items[index] = entry(content, code=items[index]['version_code'], abi=items[index]['abi'])
        (root / (items[index]['sha256'] + '.apk')).write_bytes(content)
    document = read_manifest(root)
    document['updates'] = items
    (root / 'manifest.json').write_text(json.dumps(document))
    assert version(client, 4008).json()['update']['version_code'] == 2**32 + 4008
    assert version(client, MAX_VERSION_CODE).json() == {'available': False, 'update': None}


@pytest.mark.parametrize('code,abi', [(-1, 'arm64-v8a'), ('abc', 'arm64-v8a'),
    (MAX_VERSION_CODE + 1, 'arm64-v8a'), (0, 'arm64'), (0, '../arm64-v8a'), (0, 'arm64-v8a\n')])
def test_invalid_query(phone, code, abi):
    assert version(phone[0], code, abi).status_code == 422


def test_full_download_and_lan_auth(phone):
    client, root = phone
    item = write_catalog(root)
    for headers in ({}, {'Authorization': 'Bearer wrong'}, {'X-Forwarded-For': '127.0.0.1'}):
        for path in ['/api/app/version?version_code=0&abi=arm64-v8a', item['download_path']]:
            response = client.get(path, headers=headers)
            assert response.status_code == 401
            assert response.headers['www-authenticate'] == 'Bearer'
    response = client.get(item['download_path'], headers={**HEADERS, 'Range': 'bytes=0-3'})
    assert response.status_code == 200  # Contract serves the full APK, including for Range requests.
    assert len(response.content) == item['size_bytes']
    assert hashlib.sha256(response.content).hexdigest() == item['sha256']
    assert response.headers['content-length'] == str(item['size_bytes'])
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.headers['content-type'] == 'application/vnd.android.package-archive'
    assert client.get('/runtime/app-updates/manifest.json', headers=HEADERS).status_code == 404
    assert client.get('/runtime/app-updates/' + item['sha256'] + '.apk', headers=HEADERS).status_code == 404


@pytest.mark.parametrize('damage', ['missing', 'size', 'same-size', 'same-stat-size'])
def test_missing_corrupt_and_hash_not_cached(phone, damage):
    client, root = phone
    item = write_catalog(root)
    assert version(client).json()['available']
    assert client.get(item['download_path'], headers=HEADERS).status_code == 200
    apk = root / (item['sha256'] + '.apk')
    previous = apk.stat()
    if damage == 'missing':
        apk.unlink()
    else:
        apk.write_bytes(b'x' * (item['size_bytes'] + (1 if damage == 'size' else 0)))
        if damage == 'same-stat-size':
            os.utime(apk, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    assert version(client).status_code == 503
    assert client.get(item['download_path'], headers=HEADERS).status_code == 404


@pytest.mark.parametrize('value', ['{}', '[]', '{broken', '{"schema_version":1,"schema_version":1}',
    '{"schema_version":true,"certificate_sha256":"' + CERT + '","updates":[]}'])
def test_malformed_manifest_fails_closed(phone, value):
    client, root = phone
    item = write_catalog(root)
    (root / 'manifest.json').write_text(value)
    assert version(client).status_code == 503
    assert client.get(item['download_path'], headers=HEADERS).status_code == 503


@pytest.mark.parametrize('change', [dict(version_code=True), dict(version_code=0), dict(version_code=MAX_VERSION_CODE + 1),
    dict(size_bytes=True), dict(size_bytes=-1), dict(sha256='../private'), dict(abi=['arm64-v8a']),
    dict(package_name='com.other.app'), dict(download_path='https://other.invalid/a.apk'),
    dict(download_path='/api/app/apk/../private'), dict(notes=None)])
def test_invalid_manifest_fields(phone, change):
    client, root = phone
    item = entry()
    write_catalog(root, [{**item, **change}])
    assert version(client).status_code == 503


@pytest.mark.parametrize('suffix', ['../manifest.json', '%2e%2e%2fmanifest.json', '..%5cmanifest.json',
    'a' * 64 + '.apk', 'A' * 64, 'b' * 64, '%2fetc%2fpasswd'])
def test_traversal_and_unpublished_digests(phone, suffix):
    client, root = phone
    write_catalog(root)
    assert client.get('/api/app/apk/' + suffix, headers=HEADERS).status_code == 404


@pytest.mark.parametrize('target', ['apk', 'manifest', 'root'])
def test_symlinks_rejected(phone, tmp_path, target):
    client, root = phone
    item = write_catalog(root)
    if target == 'root':
        outside = tmp_path / 'outside'
        root.rename(outside)
        link, destination = root, outside
    else:
        link = root / ('manifest.json' if target == 'manifest' else item['sha256'] + '.apk')
        destination = tmp_path / ('external-' + link.name)
        link.rename(destination)
    try:
        link.symlink_to(destination, target_is_directory=target == 'root')
    except OSError as exc:
        pytest.skip(f'Host does not permit symlink creation: {exc}')
    assert version(client).status_code == 503
    assert client.get(item['download_path'], headers=HEADERS).status_code in (404, 503)


@pytest.mark.parametrize('cloud', [False, True])
def test_updater_not_registered_on_main_or_cloud(settings, cloud):
    app = video_app(replace(settings, cloud_mode=cloud), MockVisionProvider())
    assert not any(getattr(route, 'path', '').startswith('/api/app/') for route in app.routes)
    with TestClient(app, base_url='http://localhost') as client:
        assert client.get('/api/app/version?version_code=0&abi=arm64-v8a').status_code == 404
        assert client.get('/api/app/apk/' + 'a' * 64).status_code == 404
    if cloud:
        with pytest.raises(ValueError, match='separately from cloud'):
            phone_app.create_app(replace(settings, cloud_mode=True), MockVisionProvider())


def synthetic_apk(path, abi='arm64-v8a'):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(f'lib/{abi}/libsynthetic.so', b'not a real library')
        archive.writestr('AndroidManifest.xml', b'not a real manifest')
    return path


@pytest.fixture
def tool_output(monkeypatch):
    output = {
        'badging': f"package: name='{PACKAGE_NAME}' versionCode='4008' versionName='1.2.3'\nnative-code: 'arm64-v8a'\n",
        'tree': '    A: android:versionCode(0x0101021b)=(type 0x10)0xfa8\n',
        'signature': 'Signer #1 certificate DN: CN=Synthetic Release\nSigner #1 certificate SHA-256 digest: ' + CERT + '\n',
    }

    def run(args):
        if 'badging' in args:
            return output['badging']
        if 'xmltree' in args:
            return output['tree']
        return output['signature']

    monkeypatch.setattr(publisher, 'run_tool', run)
    return output


TOOLS = (Path('synthetic-aapt2'), Path('synthetic-apksigner.jar'), Path('synthetic-java'))


def test_publisher_first_pin_actual_metadata_content_and_idempotency(tmp_path, tool_output):
    apk = synthetic_apk(tmp_path / 'input.apk')
    root = tmp_path / 'updates'
    with pytest.raises(InvalidUpdate, match='release certificate'):
        publisher.publish([apk], 'test', root=root, tools=TOOLS)
    assert not (root / 'manifest.json').exists()
    # There is deliberately no version or ABI override accepted by the CLI.
    tool_output['tree'] += '    A: android:versionCodeMajor(0x01010576)=(type 0x10)0x1\n'
    updates = publisher.publish([apk], '真实 APK 元数据', root=root, tools=TOOLS, expected_certificate=CERT)
    assert updates[0]['version_code'] == 2**32 + 4008
    assert updates[0]['abi'] == 'arm64-v8a'
    assert (root / (updates[0]['sha256'] + '.apk')).read_bytes() == apk.read_bytes()
    assert read_manifest(root)['updates'] == updates
    publisher.publish([apk], 'second', root=root, tools=TOOLS)
    assert len(read_manifest(root)['updates']) == 1
    assert not list(root.glob('.publish-*')) and not (root / '.publish.lock').exists()


@pytest.mark.parametrize('change', ['package', 'debuggable', 'debug-cert', 'wrong-cert', 'unsigned', 'multi-signer',
    'bad-code', 'native-abi', 'duplicate-apk'])
def test_publisher_rejects_invalid_apk_before_manifest(tmp_path, tool_output, change):
    apk = synthetic_apk(tmp_path / 'input.apk')
    root = tmp_path / 'updates'
    if change == 'package':
        tool_output['badging'] = tool_output['badging'].replace(PACKAGE_NAME, PACKAGE_NAME + '.test')
    elif change == 'debuggable':
        tool_output['badging'] += 'application-debuggable\n'
    elif change == 'debug-cert':
        tool_output['signature'] = tool_output['signature'].replace('Synthetic Release', 'Android Debug')
    elif change == 'wrong-cert':
        tool_output['signature'] = tool_output['signature'].replace(CERT, 'b' * 64)
    elif change == 'unsigned':
        tool_output['signature'] = 'Does not verify'
    elif change == 'multi-signer':
        tool_output['signature'] += 'Signer #2 certificate SHA-256 digest: ' + 'b' * 64 + '\n'
    elif change == 'bad-code':
        tool_output['tree'] = tool_output['tree'].replace('0xfa8', '0xfa9')
    elif change == 'native-abi':
        tool_output['badging'] = tool_output['badging'].replace("'arm64-v8a'", "'x86_64'")
    with pytest.raises(InvalidUpdate):
        publisher.publish([apk, apk] if change == 'duplicate-apk' else [apk], 'test', root=root,
                          tools=TOOLS, expected_certificate=CERT)
    assert not (root / 'manifest.json').exists()
    assert not list(root.glob('*.apk'))


def test_failed_batch_preserves_old_manifest_and_apk(tmp_path, tool_output, monkeypatch):
    apk = synthetic_apk(tmp_path / 'input.apk')
    root = tmp_path / 'updates'
    original = publisher.publish([apk], 'first', root=root, tools=TOOLS, expected_certificate=CERT)
    before = (root / 'manifest.json').read_bytes()
    original_inspect = publisher.inspect_apk
    count = 0

    def fail_second(path, tools):
        nonlocal count
        count += 1
        if count == 2:
            raise InvalidUpdate('Synthetic second input failure')
        return original_inspect(path, tools)

    monkeypatch.setattr(publisher, 'inspect_apk', fail_second)
    with pytest.raises(InvalidUpdate, match='second input'):
        publisher.publish([apk, apk], 'new', root=root, tools=TOOLS)
    assert (root / 'manifest.json').read_bytes() == before
    assert (root / (original[0]['sha256'] + '.apk')).read_bytes() == apk.read_bytes()
    with pytest.raises(InvalidUpdate, match='already published certificate'):
        publisher.publish([apk], 'new', root=root, tools=TOOLS, expected_certificate='b' * 64)
    assert (root / 'manifest.json').read_bytes() == before


def test_retains_old_immutable_downloads_and_rejects_same_code_replacement(tmp_path, tool_output):
    apk = synthetic_apk(tmp_path / 'input.apk')
    root = tmp_path / 'updates'
    first = publisher.publish([apk], 'first', root=root, tools=TOOLS, expected_certificate=CERT)
    with zipfile.ZipFile(apk, 'a') as archive:
        archive.writestr('assets/new.txt', 'synthetic new release')
    with pytest.raises(InvalidUpdate, match='already published ABI/version'):
        publisher.publish([apk], 'replacement', root=root, tools=TOOLS)
    tool_output['tree'] = tool_output['tree'].replace('0xfa8', '0xfa9')
    tool_output['badging'] = tool_output['badging'].replace("'4008'", "'4009'")
    second = publisher.publish([apk], 'second', root=root, tools=TOOLS)
    assert len(read_manifest(root)['updates']) == 2
    assert (root / (first[0]['sha256'] + '.apk')).exists()
    assert (root / (second[0]['sha256'] + '.apk')).exists()
    tool_output['tree'] = tool_output['tree'].replace('0xfa9', '0xfa7')
    tool_output['badging'] = tool_output['badging'].replace("'4009'", "'4007'")
    with pytest.raises(InvalidUpdate, match='downgrade'):
        publisher.publish([apk], 'older', root=root, tools=TOOLS)


def test_publish_lock_and_atomic_manifest_failure(tmp_path, tool_output, monkeypatch):
    apk = synthetic_apk(tmp_path / 'input.apk')
    root = tmp_path / 'updates'
    publisher.publish([apk], 'first', root=root, tools=TOOLS, expected_certificate=CERT)
    before = (root / 'manifest.json').read_bytes()
    (root / '.publish.lock').touch()
    with pytest.raises(InvalidUpdate, match='Another publisher'):
        publisher.publish([apk], 'new', root=root, tools=TOOLS)
    (root / '.publish.lock').unlink()

    def fail_replace(*args):
        raise OSError('Synthetic interrupted replace')

    monkeypatch.setattr(publisher.os, 'replace', fail_replace)
    with pytest.raises(OSError, match='interrupted'):
        publisher.publish([apk], 'new', root=root, tools=TOOLS)
    assert (root / 'manifest.json').read_bytes() == before
    assert not (root / '.publish.lock').exists()


def test_all_three_split_abis_publish_together(tmp_path, monkeypatch):
    apks = [synthetic_apk(tmp_path / f'{abi}.apk', abi) for abi in ('arm64-v8a', 'armeabi-v7a', 'x86_64')]
    codes = {'armeabi-v7a': 5009, 'arm64-v8a': 6009, 'x86_64': 7009}

    def inspect(path, tools):
        with zipfile.ZipFile(path) as archive:
            abi = archive.namelist()[0].split('/')[1]
        return {'version_name': '1.2.4', 'version_code': codes[abi], 'abis': [abi],
                'certificate_sha256': publisher.DEFAULT_RELEASE_CERT_SHA256}

    monkeypatch.setattr(publisher, 'inspect_apk', inspect)
    updates = publisher.publish(apks, '1.2.4 update', root=tmp_path / 'updates', tools=TOOLS)
    assert {item['abi']: item['version_code'] for item in updates} == codes
    assert read_manifest(tmp_path / 'updates')['certificate_sha256'] == publisher.DEFAULT_RELEASE_CERT_SHA256


def test_size_and_notes_limits_before_tool_calls(tmp_path, monkeypatch):
    def forbidden(*args):
        pytest.fail('Invalid size/notes must be rejected before invoking tools')

    monkeypatch.setattr(publisher, 'inspect_apk', forbidden)
    apk = tmp_path / 'oversized.apk'
    with apk.open('wb') as output:
        output.truncate(MAX_APK_BYTES + 1)
    with pytest.raises(InvalidUpdate, match='256 MiB'):
        publisher.publish([apk], '', root=tmp_path / 'updates', tools=TOOLS, expected_certificate=CERT)
    with pytest.raises(InvalidUpdate, match='10000'):
        publisher.publish([apk], '字' * (MAX_NOTES_CHARS + 1), root=tmp_path / 'updates', tools=TOOLS)
    assert not (tmp_path / 'updates/manifest.json').exists()


def test_manifest_size_and_notes_boundaries():
    from backend.app_updates import validate_manifest
    document = {'schema_version': 1, 'certificate_sha256': CERT,
                'updates': [{**entry(), 'size_bytes': MAX_APK_BYTES, 'notes': '字' * MAX_NOTES_CHARS}]}
    assert validate_manifest(document) is document
    for key, value in [('size_bytes', MAX_APK_BYTES + 1), ('notes', 'a' * (MAX_NOTES_CHARS + 1))]:
        invalid = {**document, 'updates': [{**document['updates'][0], key: value}]}
        with pytest.raises(InvalidUpdate):
            validate_manifest(invalid)


def test_explicit_tools_and_sdk_discovery(tmp_path, monkeypatch):
    aapt = tmp_path / ('aapt2.exe' if os.name == 'nt' else 'aapt2')
    signer = tmp_path / 'apksigner.jar'
    java = tmp_path / ('java.exe' if os.name == 'nt' else 'java')
    for path in (aapt, signer, java):
        path.touch()
    assert publisher.android_tools(aapt=aapt, apksigner=signer, java=java) == (aapt, signer, java)
    with pytest.raises(InvalidUpdate, match='both --aapt'):
        publisher.android_tools(aapt=aapt, java=java)
    monkeypatch.delenv('JAVA_HOME', raising=False)
    monkeypatch.setattr(publisher.shutil, 'which', lambda name: None)
    with pytest.raises(InvalidUpdate, match='--java'):
        publisher.android_tools(aapt=aapt, apksigner=signer)
    executable_signer = tmp_path / 'apksigner.bat'
    executable_signer.touch()
    assert publisher.android_tools(aapt=aapt, apksigner=executable_signer) == (aapt, executable_signer, None)
    sdk = tmp_path / 'sdk'
    for version in ('9.0.0', '36.0.0', '37.0.0-rc1'):
        directory = sdk / 'build-tools' / version
        (directory / 'lib').mkdir(parents=True)
        (directory / aapt.name).touch()
        (directory / 'lib/apksigner.jar').touch()
    assert publisher.android_tools(sdk=sdk, java=java)[0] == sdk / 'build-tools/36.0.0' / aapt.name


@pytest.mark.parametrize('linked', ['file', 'parent'])
def test_windows_reparse_points_fail_closed_without_symlink_privilege(tmp_path, monkeypatch, linked):
    from types import SimpleNamespace
    from backend.app_updates import private_path
    root = tmp_path / 'runtime/app-updates'
    root.mkdir(parents=True)
    target = root / 'manifest.json'
    target.write_text('{}')
    actual = Path.lstat
    reparse = target if linked == 'file' else root.parent

    def lstat(path, *args, **kwargs):
        info = actual(path, *args, **kwargs)
        if path == reparse:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path, 'lstat', lstat)
    with pytest.raises(InvalidUpdate, match='links or reparse'):
        private_path(target)


def test_failed_android_tool_never_leaks_diagnostics(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(publisher.subprocess, 'run', lambda *a, **kw:
                        SimpleNamespace(returncode=1, stdout='synthetic-secret', stderr='synthetic-secret'))
    with pytest.raises(InvalidUpdate, match='Android verification failed') as result:
        publisher.run_tool(['synthetic-aapt2', 'dump', 'badging', 'synthetic.apk'])
    assert 'synthetic-secret' not in str(result.value)


def test_version_name_limit_matches_native_contract():
    from backend.app_updates import validate_manifest
    document = {'schema_version': 1, 'certificate_sha256': CERT,
                'updates': [{**entry(), 'version_name': 'v' * 100}]}
    assert validate_manifest(document) is document
    document['updates'][0]['version_name'] += 'v'
    with pytest.raises(InvalidUpdate):
        validate_manifest(document)
