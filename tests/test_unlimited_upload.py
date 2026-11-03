"""Upload-limit tests: synthetic media only, no AI/provider requests."""
from dataclasses import replace
import math
import struct

from fastapi.testclient import TestClient
import pytest

from backend.main import create_app
from backend import media
from backend.errors import AppError
from tests.conftest import MockVisionProvider


def test_zero_disables_upload_limits_and_accepts_over_50mib(settings,sample_video,tmp_path):
    large=tmp_path/'synthetic-test-only-over-50mib.mp4'
    # A valid ISO-BMFF free box pads the synthetic clip without changing frames.
    with large.open('wb') as out:
        out.write(sample_video.read_bytes())
        padding=51*1024**2
        out.write(struct.pack('>I4s',padding,b'free'))
        out.seek(padding-9,1)
        out.write(b'\0')
    config=replace(settings,cloud_mode=True,cloud_cookie_secure=False,
        cloud_storage_mb=0,max_upload_bytes=0,max_video_seconds=0)
    app=create_app(config,MockVisionProvider())
    with TestClient(app,base_url='http://localhost') as client:
        limits=client.get('/api/health').json()['limits']
        assert limits['max_upload_mb'] is None and limits['max_video_seconds'] is None
        with large.open('rb') as stream:
            response=client.post('/api/videos/upload',content=stream,
                headers={'Content-Length':str(large.stat().st_size),'X-Video-Name':large.name})
        assert response.status_code==201,response.text
        vid=response.json()['video_id']
        assert app.state.db.video(vid)['metadata']['file_size_bytes']>50*1024**2
        assert client.post(f'/api/videos/{vid}/delete').status_code==200
        # The multipart path must obey the same disabled-limit setting.
        with large.open('rb') as stream:
            response=client.post('/api/videos',files={'file':(large.name,stream,'video/mp4')})
        assert response.status_code==201,response.text
        assert client.post(f"/api/videos/{response.json()['video_id']}/delete").status_code==200
        assert app.state.db.usage_summary()['requests']==0


def test_unlimited_duration_accepts_long_metadata_but_rejects_invalid(settings,sample_video,monkeypatch):
    info=media.command_json([settings.ffprobe,'-v','error','-show_streams','-show_format','-print_format','json',str(sample_video)])
    stream=next(s for s in info['streams'] if s['codec_type']=='video')
    monkeypatch.setattr(media,'command_json',lambda args:info)
    stream['duration']='90001'  # Admission test only, not a fabricated long-video run.
    assert media.inspect_video(sample_video,replace(settings,max_video_seconds=0))['duration_ms']==90001000
    with pytest.raises(AppError,match='不超过'):
        media.inspect_video(sample_video,replace(settings,max_video_seconds=120))
    for duration in [0,-1,math.inf,math.nan]:
        stream['duration']=str(duration)
        with pytest.raises(AppError):
            media.inspect_video(sample_video,replace(settings,max_video_seconds=0))


def test_unlimited_upload_still_checks_real_disk(settings,sample_video,monkeypatch):
    import shutil
    config=replace(settings,cloud_mode=True,cloud_cookie_secure=False,
        cloud_storage_mb=0,max_upload_bytes=0,max_video_seconds=0)
    app=create_app(config,MockVisionProvider())
    with TestClient(app,base_url='http://localhost') as client:
        monkeypatch.setattr(shutil,'disk_usage',lambda path:type('Disk',(),{'free':1024})())
        response=client.post('/api/videos/upload',content=sample_video.read_bytes())
        assert response.status_code==413 and response.json()['error']['code']=='DISK_SPACE'
        assert client.get('/api/videos').json()['videos']==[]


@pytest.mark.parametrize('field',['max_video_seconds','max_upload_bytes','cloud_storage_mb'])
def test_negative_limits_are_rejected(settings,field):
    with pytest.raises(ValueError):
        replace(settings,**{field:-1})


def test_chunk_upload_integrity_isolation_cancellation_and_restart(settings,sample_video):
    import hashlib
    from backend.uploads import CHUNK_BYTES
    config=replace(settings,cloud_mode=True,cloud_cookie_secure=False,
        cloud_storage_mb=0,max_upload_bytes=0,max_video_seconds=0,cloud_videos_per_visitor=1)
    app=create_app(config,MockVisionProvider())
    payload=sample_video.read_bytes()
    with TestClient(app,base_url='http://localhost') as first:
        uid=first.post('/api/uploads',json={'filename':'test-only.mp4','size':len(payload)}).json()['upload_id']
        assert first.post('/api/uploads',json={'filename':'test.mp4','size':1}).status_code==429
        with TestClient(app,base_url='http://localhost') as other:
            for suffix in ['chunks?offset=0','complete','cancel']:
                assert other.post(f'/api/uploads/{uid}/{suffix}',content=b'x').status_code==404
        assert first.post(f'/api/uploads/{uid}/complete').status_code==409
        assert first.post(f'/api/uploads/{uid}/chunks?offset=1',content=b'x').status_code==409
        assert first.post(f'/api/uploads/{uid}/chunks?offset=0',content=b'x'*(CHUNK_BYTES+1)).status_code==413
        for offset in range(0,len(payload),4096):
            r=first.post(f'/api/uploads/{uid}/chunks?offset={offset}',content=payload[offset:offset+4096])
            assert r.status_code==200,r.text
        assert first.post(f'/api/uploads/{uid}/complete').status_code==201
        assert app.state.db.video(uid)['sha256']==hashlib.sha256(payload).hexdigest()
        assert first.post(f'/api/videos/{uid}/delete').status_code==200
        cancelled=first.post('/api/uploads',json={'filename':'cancel.mp4','size':20}).json()['upload_id']
        assert first.post(f'/api/uploads/{cancelled}/cancel').status_code==200
        assert not (config.data_dir/'videos'/cancelled).exists()
        unfinished=first.post('/api/uploads',json={'filename':'restart.mp4','size':20}).json()['upload_id']
    # New process/session manager cleans only its marked incomplete transfer.
    with TestClient(create_app(config,MockVisionProvider()),base_url='http://localhost') as fresh:
        assert not (config.data_dir/'videos'/unfinished).exists()
        assert fresh.get('/api/videos').json()['videos']==[]
