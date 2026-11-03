"""Verify real FFmpeg recording, playable segments and cleanup using synthetic G media."""
from __future__ import annotations
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ai-service'))


def wait_for(predicate, seconds):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        value=predicate()
        if value: return value
        time.sleep(.1)
    raise AssertionError('bounded_condition_timeout')


def main():
    import cv2
    from ai_service.config import RecordingConfig
    from ai_service.recording import CameraRecorder,RecordingStore
    output=ROOT/'runtime'/('recording-validation-'+uuid.uuid4().hex[:8]);output.mkdir()
    tool=ROOT/'tools/ffmpeg/ffmpeg.exe'
    source=output/'synthetic-source.mkv'
    generated=subprocess.run([str(tool),'-hide_banner','-loglevel','error','-n','-f','lavfi',
        '-i','testsrc2=size=160x96:rate=10','-t','8','-c:v','mpeg4','-g','10','-q:v','5',
        '-an',str(source)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=15)
    assert generated.returncode==0,'fixture_generation_failed'
    directory=output/'recordings'
    config=RecordingConfig(enabled=True,directory=str(directory),ffmpeg_path=str(tool),
        segment_seconds=2,retention_days=3.,max_gib=.05,min_free_gib=.001,cleanup_seconds=1.)
    recorder=CameraRecorder(config,str(source),local_file=True)
    snapshots=[]
    try:
        wait_for(lambda:recorder.status()['state']=='recording',10)
        wait_for(lambda:len(list(directory.glob('*.mkv')))>=3,10)
        snapshots.append(recorder.status())
    finally: recorder.close()
    assert recorder.thread is not None and not recorder.thread.is_alive()
    assert recorder.process is None
    files=sorted(directory.glob('*.mkv'))
    decoded=[]
    for file in files:
        capture=cv2.VideoCapture(str(file));ok,image=capture.read();capture.release()
        assert ok and image is not None, 'closed_segment_not_playable'
        decoded.append({'filename':file.name,'bytes':file.stat().st_size,'first_frame_size':list(image.shape[:2])})
    assert len(files)>=3
    unrelated=directory/'user-file.mkv';unrelated.write_bytes(b'do-not-delete')
    # Force an owned completed segment to be expired, then verify automatic
    # startup cleanup on a new recorder session, not just a direct unit call.
    expired=files[0];old=time.time()-4*86400;os.utime(expired,(old,old))
    again=CameraRecorder(config,str(source),local_file=True)
    try:
        wait_for(lambda:not expired.exists(),5)
        wait_for(lambda:again.status()['state']=='recording',10)
        periodic_expired=files[1]
        os.utime(periodic_expired,(old,old))
        wait_for(lambda:not periodic_expired.exists(),5)
        snapshots.append(again.status())
    finally: again.close()
    assert unrelated.read_bytes()==b'do-not-delete'
    assert not again.thread.is_alive() and again.process is None
    # A full quota pauses without deleting a currently open segment; unit tests
    # cover active-file protection and unreclaimable free-space conditions.
    report={'passed':True,'actual_ffmpeg':True,'source':'generated synthetic 160x96 video',
        'camera_connected':False,'credentials_used':False,'cloud_uploads':False,
        'audio_recorded':False,'stream_copy':True,'segments_playable':decoded,
        'periodic_cleanup_seconds_in_test':1,'automatic_expired_cleanup':not expired.exists(),
        'periodic_expired_cleanup_while_recording':not periodic_expired.exists(),
        'unrelated_file_preserved':unrelated.exists(),'owned_children_stopped':True,
        'status_snapshots':snapshots,'test_output_directory':str(output)}
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
