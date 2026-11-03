"""Run the actual GPU web worker on authorized local video; no camera or alarm relay.

Child substitutes only source and alert sink. JSON/JPEG protocol, annotation,
models, concurrency and detection engine are the production implementations.
"""
from __future__ import annotations
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import runpy
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]


def child(recording_directory=None):
    sys.path.insert(0,str(ROOT/'ai-service'))
    import cv2
    from ai_service import sources, preview_alerts, recording
    from dataclasses import replace
    real_recorder = recording.CameraRecorder
    if recording_directory is None:
        recording.CameraRecorder = lambda config, url: real_recorder(replace(config, enabled=False), url)
    else:
        recording_directory=recording_directory.resolve()
        if not recording_directory.is_relative_to((ROOT/'runtime').resolve()):
            raise ValueError('recording fixture must stay in runtime')
        recording.CameraRecorder = lambda config, url: real_recorder(
            replace(config, directory=str(recording_directory), segment_seconds=1,
                    cleanup_seconds=1., max_gib=.05, min_free_gib=.001),
            str(ROOT/'runtime/validation-media/urfd-fall-06-cam0.mp4'),local_file=True)
    frames=[]
    capture=cv2.VideoCapture(str(ROOT/'runtime/validation-media/urfd-fall-06-cam0.mp4'))
    while True:
        ok,image=capture.read()
        if not ok: break
        frames.append(cv2.resize(image,(2880,1620)))
    capture.release()
    assert frames
    class FileLink:
        def __init__(self,url,config): self.start=None;self.sequence=0
        def open(self,stop): self.start=time.perf_counter();return True
        def read(self,stop):
            now=time.perf_counter()
            index=min(len(frames)-1,int((now-self.start)*30))
            self.sequence+=1
            return 'frame',sources.CapturedFrame(frames[index],now,datetime.now(timezone.utc),self.sequence)
        def close(self): pass
    class SilentAlerts:
        def __init__(self,*args,**kwargs): pass
        def status(self): return {'enabled':False,'test_sink':True}
        def mark_frame(self): pass
        def alarm(self,*args): return None
        def disconnected(self): pass
        def close(self): pass
    import dotenv
    dotenv.dotenv_values=lambda *args,**kwargs: {'RTSP_URL':'rtsp://test:test@127.0.0.1/fixture'}
    sources.CaptureLink=FileLink
    preview_alerts.PreviewAlerts=SilentAlerts
    runpy.run_path(str(ROOT/'scripts/web-camera-worker.py'),run_name='__main__')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--child',action='store_true')
    parser.add_argument('--report',type=Path)
    parser.add_argument('--recording-directory',type=Path)
    args=parser.parse_args()
    if args.child: child(args.recording_directory);return
    import cv2
    import numpy as np
    child_args=[sys.executable,str(Path(__file__).resolve()),'--child']
    if args.recording_directory is not None:
        child_args += ['--recording-directory',str(args.recording_directory.resolve())]
    args.report.parent.mkdir(parents=True,exist_ok=True)
    bootstrap_log=(args.report.parent/'worker-bootstrap.stderr').open('wb')
    process=subprocess.Popen(child_args,
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=bootstrap_log)
    stop=threading.Event()
    def ping():
        while not stop.wait(2):
            try: process.stdin.write(b'ping\n');process.stdin.flush()
            except (BrokenPipeError,OSError): return
    threading.Thread(target=ping,daemon=True).start()
    timer=threading.Timer(65,process.kill);timer.start()
    import struct
    packets=[];last=None;events=[];valid_jpegs=0;stop_sent=False
    def exact(n):
        data=bytearray()
        while len(data)<n:
            part=process.stdout.read(n-len(data))
            if not part: raise EOFError
            data.extend(part)
        return bytes(data)
    try:
        while True:
            length=struct.unpack('!I',exact(4))[0]
            assert 0<length<2_000_000
            info=json.loads(exact(length));jpeg=exact(info['jpeg_size'])
            if info['state']=='stopped': break
            if info['state']=='error': raise RuntimeError('worker_error')
            if jpeg:
                image=cv2.imdecode(np.frombuffer(jpeg,dtype=np.uint8),cv2.IMREAD_COLOR)
                assert image is not None and image.shape[:2]==(720,1280)
                valid_jpegs+=1;packets.append(info);last=info
            if valid_jpegs>=90 and not stop_sent:
                process.stdin.write(b'stop\n');process.stdin.flush();stop_sent=True
        process.wait(timeout=12)
        assert process.returncode==0
        assert last and last['fire_state']=='running'
        events=last['events']
        assert any(e['event_type']=='suspected_fall' for e in events), 'fall_not_detected_in_worker'
        recording_check=None
        if args.recording_directory is not None:
            assert any(p.get('recording') is True for p in packets), 'worker_never_recorded'
            assert info['recording_status']['state']=='stopped' and info['recording'] is False
            decoded=[]
            for path in args.recording_directory.glob('*.mkv'):
                cap=cv2.VideoCapture(str(path));ok,frame=cap.read();cap.release()
                assert ok and frame is not None
                decoded.append({'filename':path.name,'bytes':path.stat().st_size})
            assert decoded
            recording_check={'recording_observed':True,'segments_playable':decoded,
                             'stop_status':info['recording_status'],'source':'existing local fall video'}
        report={'passed':True,'source':'local prerecorded fall video upscaled to 2880x1620',
            'real_web_worker':True,'real_gpu_models':True,'camera_connected':False,'recording_check':recording_check,
            'alarm_delivery':False,'media_saved':recording_check is not None,'valid_jpeg_packets':valid_jpegs,
            'status':last,'trace':[{'frames':p['frames'],'occurred_at':p['occurred_at'],'fall_diagnostics':p.get('fall_diagnostics',[]),'fps':p.get('fps')} for p in packets], 'event_types':sorted(set(e['event_type'] for e in events))}
    finally:
        bootstrap_log.close()
        stop.set();timer.cancel()
        if process.poll() is None: process.kill();process.wait(timeout=10)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
