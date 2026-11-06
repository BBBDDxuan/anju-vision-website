"""Windows reader locks must not abort an otherwise valid video scan."""
import json
import pytest

import ai_service.safety_candidates as module


def windows_error(code):
    error = PermissionError('synthetic file sharing conflict')
    error.winerror = code
    return error


@pytest.mark.parametrize('code', [5, 32, 33])
def test_transient_windows_reader_lock_retries_same_flushed_payload(tmp_path, monkeypatch, code):
    path = tmp_path / 'scan.json'
    path.write_text('{"completed":false}', encoding='utf-8')
    original = module.os.replace
    calls = []

    def locked_once(source, destination):
        calls.append(source)
        if len(calls) == 1:
            assert json.loads(path.read_text('utf-8')) == {'completed': False}
            raise windows_error(code)
        original(source, destination)

    monkeypatch.setattr(module.os, 'replace', locked_once)
    module.atomic_json(path, {'completed': True})
    assert len(calls) == 2 and calls[0] == calls[1]
    assert json.loads(path.read_text('utf-8')) == {'completed': True}
    assert not list(tmp_path.glob('*.partial'))


def test_persistent_windows_lock_is_bounded_and_preserves_old_commit(tmp_path, monkeypatch):
    path = tmp_path / 'scan.json'
    path.write_text('{"completed":false}', encoding='utf-8')
    calls = []

    def locked(source, destination):
        calls.append(source)
        raise windows_error(32)

    monkeypatch.setattr(module.os, 'replace', locked)
    with pytest.raises(PermissionError):
        module.atomic_json(path, {'completed': True})
    assert len(calls) == 5
    assert json.loads(path.read_text('utf-8')) == {'completed': False}
    assert len(list(tmp_path.glob('*.partial'))) == 1


def test_non_sharing_write_error_is_not_retried(tmp_path, monkeypatch):
    calls = []

    def failed(source, destination):
        calls.append(source)
        raise OSError('synthetic disk failure')

    monkeypatch.setattr(module.os, 'replace', failed)
    with pytest.raises(OSError):
        module.atomic_json(tmp_path / 'scan.json', {'completed': True})
    assert len(calls) == 1
