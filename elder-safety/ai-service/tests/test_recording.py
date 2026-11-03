"""Ownership-scoped retention and recorder lifecycle in a dedicated folder."""
from dataclasses import replace
import json
import os
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from ai_service.config import Config, RecordingConfig, update
from ai_service.recording import GIB, CameraRecorder, RecordingStore, recording_command


def cfg(**kwargs):
    return RecordingConfig(retention_days=3.,max_gib=10.,min_free_gib=1.,**kwargs)


def store(tmp_path, **kwargs):
    return RecordingStore(tmp_path/'archive',**kwargs)


def segment(owner,prefix,index,size=10,age=0):
    path=owner.root/f'{prefix}_{index:06d}.mkv'
    path.write_bytes(b'x'*size)
    modified=time.time()-age
    os.utime(path,(modified,modified))
    return path


def test_expiry_only_deletes_owned_old_video_never_unrelated_or_active(tmp_path):
    owner=store(tmp_path)
    try:
        prefix=owner.new_session()
        old=segment(owner,prefix,0,age=4*86400)
        recent=segment(owner,prefix,1)
        active=segment(owner,prefix,2,age=5*86400)
        user=owner.root/'family-video.mkv';user.write_bytes(b'keep')
        fake=owner.root/'camera_20000101T000000Z_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_000001.mkv'
        fake.write_bytes(b'unregistered');os.utime(fake,(time.time()-10*86400,time.time()-10*86400))
        sub=owner.root/'family';sub.mkdir();(sub/'video.mkv').write_bytes(b'keep')
        result=owner.cleanup(cfg(),active=active)
        assert not old.exists()
        assert recent.exists() and active.exists() and user.exists() and fake.exists()
        assert (sub/'video.mkv').read_bytes()==b'keep'
        assert result['deleted_files']==1 and result['files']==2
    finally: owner.close()


def test_quota_deletes_oldest_completed_and_protects_active(tmp_path):
    owner=store(tmp_path)
    try:
        prefix=owner.new_session()
        oldest=segment(owner,prefix,0,size=20,age=20)
        newer=segment(owner,prefix,1,size=20,age=10)
        active=segment(owner,prefix,2,size=20)
        result=owner.cleanup(replace(cfg(),max_gib=30/GIB),active)
        assert not oldest.exists() and not newer.exists() and active.exists()
        assert result['bytes']==20 and result['can_record']
    finally: owner.close()


def test_low_free_space_reclaims_only_owned_completed(tmp_path):
    initial=100
    def usage(directory):
        used=sum(p.stat().st_size for p in directory.glob('*.mkv'))
        return SimpleNamespace(free=initial-used)
    owner=store(tmp_path,disk_usage=usage)
    try:
        prefix=owner.new_session()
        old=segment(owner,prefix,0,30,age=20)
        active=segment(owner,prefix,1,30)
        result=owner.cleanup(replace(cfg(),min_free_gib=60/GIB),active)
        assert not old.exists() and active.exists() and result['free_bytes']==70
    finally: owner.close()


def test_active_file_exceeding_quota_reports_pause_without_unlink(tmp_path):
    owner=store(tmp_path)
    try:
        prefix=owner.new_session();active=segment(owner,prefix,0,30)
        result=owner.cleanup(replace(cfg(),max_gib=20/GIB),active)
        assert not result['can_record'] and active.exists()
    finally: owner.close()


def test_always_low_disk_with_no_owned_files_cannot_record(tmp_path):
    owner=store(tmp_path,disk_usage=lambda _:SimpleNamespace(free=0))
    try: assert not owner.cleanup(cfg())['can_record']
    finally: owner.close()


def test_old_session_reclaimed_after_restart_and_lock_is_exclusive(tmp_path):
    owner=store(tmp_path)
    prefix=owner.new_session();old=segment(owner,prefix,0,age=4*86400)
    try:
        with pytest.raises(ValueError,match='busy'): store(tmp_path)
    finally: owner.close()
    reopened=store(tmp_path)
    try:
        assert reopened.cleanup(cfg())['deleted_files']==1 and not old.exists()
    finally: reopened.close()


def test_nonempty_unowned_directory_is_rejected_and_left_intact(tmp_path):
    target=tmp_path/'archive';target.mkdir();data=target/'user.mkv';data.write_bytes(b'original')
    with pytest.raises(ValueError,match='nonempty_unowned'): store(tmp_path)
    assert data.read_bytes()==b'original'
    assert not (target/'.elder-safety-recordings.json').exists()


def test_owner_marker_tampering_prevents_cleanup(tmp_path):
    owner=store(tmp_path)
    try:
        prefix=owner.new_session();old=segment(owner,prefix,0,age=5*86400)
        (owner.root/'.elder-safety-recordings.json').write_text('{}')
        with pytest.raises(ValueError,match='owner_changed'): owner.cleanup(cfg())
        assert old.exists()
    finally: owner.close()


def test_session_marker_tampering_excludes_files_from_deletion(tmp_path):
    owner=store(tmp_path)
    try:
        prefix=owner.new_session();old=segment(owner,prefix,0,age=5*86400)
        (owner.root/('.'+prefix+'.json')).write_text('{}')
        assert owner.cleanup(cfg())['deleted_files']==0
        assert old.exists()
    finally: owner.close()


def test_reparse_target_is_never_unlinked(tmp_path,monkeypatch):
    from ai_service import recording
    owner=store(tmp_path)
    try:
        prefix=owner.new_session();old=segment(owner,prefix,0,age=5*86400)
        original=recording.plain_path
        monkeypatch.setattr(recording,'plain_path',lambda path:False if path==old else original(path))
        assert owner.cleanup(cfg())['deleted_files']==0
        assert old.exists()
    finally: owner.close()


def test_command_uses_stream_copy_video_only_and_segmented_mkv():
    command=recording_command('ffmpeg.exe','rtsp://test:test@localhost/stream1','output_%06d.mkv',300)
    assert command[command.index('-c:v')+1]=='copy'
    assert command[command.index('-segment_time')+1]=='300'
    assert '-an' in command and '-n' in command
    assert command[command.index('-rtsp_transport')+1]=='tcp'
    assert command[command.index('-timeout')+1]=='8000000'
    assert '-rw_timeout' not in command
    assert command[command.index('-segment_format')+1]=='matroska'
    local=recording_command('ffmpeg.exe','local.mp4','out_%06d.mkv',1,local_file=True)
    assert '-re' in local and '-rtsp_transport' not in local
    assert '-timeout' not in local


def test_disabled_recorder_never_creates_directory_or_child(tmp_path):
    directory=tmp_path/'archive'
    recorder=CameraRecorder(RecordingConfig(directory=str(directory)), 'unused')
    assert recorder.status()['state']=='disabled'
    assert recorder.process is None and recorder.thread is None and not directory.exists()
    recorder.close()


def test_missing_tool_is_safe_error_without_credentials_or_paths(tmp_path):
    recorder=CameraRecorder(RecordingConfig(enabled=True,directory=str(tmp_path/'archive'),
                            ffmpeg_path=str(tmp_path/'missing.exe')),'rtsp://private:secret@localhost/stream1')
    recorder.thread.join(timeout=2)
    status=recorder.status()
    assert status['state']=='error' and status['error']=='local_recording_failed'
    assert 'secret' not in json.dumps(status)
    recorder.close()


@pytest.mark.parametrize('options',[{'enabled':'true'},{'enabled':1},{'segment_seconds':.5},
    {'segment_seconds':0},{'segment_seconds':4000},{'max_gib':0},{'min_free_gib':-1},
    {'retention_days':float('nan')},{'cleanup_seconds':False},{'directory':'relative'},
    {'ffmpeg_path':123}])
def test_invalid_recording_configuration_rejected(options):
    with pytest.raises(ValueError):
        config=Config();update(config,{'recording':options});config.validate()


def test_old_config_does_not_enable_recording_and_camera_config_does():
    assert Config().recording.enabled is False
    root=Path(__file__).resolve().parents[3]
    config=Config.load(str(Path(__file__).parents[1]/'config.team.json'))
    assert config.recording.enabled is True
    assert Path(config.recording.directory)==root/'recordings'
    assert config.recording.segment_seconds==300 and config.recording.max_gib==10
