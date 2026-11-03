"""Real subprocess callback/resume tests; no model loads or paid/network calls."""
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest
from backend.errors import AppError
from backend.hybrid_safety import HybridSafety
import backend.hybrid_safety as hybrid


STUB = r'''
import json, os, sys, time
from pathlib import Path
args = sys.argv
output = Path(args[args.index('--output')+1])
output.mkdir(exist_ok=True)
def atomic(path, value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value),encoding='utf-8')
    os.replace(temporary,path)
atomic(output/'resume.json', {'offline_test':True})
identifier='sc_'+'a'*32
directory=output/identifier
directory.mkdir(exist_ok=True)
frames=[]
for i in range(3):
    path=directory/f'sf_{i}.jpg'
    path.write_bytes(b'offline synthetic JPEG stand-in')
    frames.append({'id':f'sf_{i}','path':str(path),'pts_ms':i*250})
candidate={'id':identifier,'event_type':'help_requested','confidence':.9,'t':.5,
    'occurred_at':'2026-10-31T00:00:00+00:00','frames':frames,'details':{}}
atomic(directory/'manifest.json',candidate)
atomic(output/'scan.json',{'version':1,'frames':1,'total_frames':100,'progress':1,
    'completed':False,'candidates':[]})
time.sleep(.15)
atomic(output/'scan.json',{'version':1,'frames':25,'total_frames':100,'progress':25,
    'completed':False,'candidates':[candidate]})
time.sleep(.8)
atomic(output/'scan.json',{'version':1,'frames':100,'total_frames':100,'progress':100,
    'completed':True,'candidates':[candidate]})
'''


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    root = tmp_path / 'stub-root'
    script = root / 'elder-safety/scripts/scan-safety-video.py'
    script.parent.mkdir(parents=True)
    script.write_text(STUB, encoding='utf-8')
    config = root / 'config.json'
    config.write_text('{"models":{}}',encoding='utf-8')
    monkeypatch.setattr(hybrid, 'ROOT', root)
    result = HybridSafety.__new__(HybridSafety)
    result.model = 'no-provider-no-network'
    result.settings = SimpleNamespace(safety_python=Path(sys.executable), safety_model_config=config, media_timeout=15)
    result.source = root / 'source.mp4'
    result.source.write_bytes(b'synthetic video identity')
    return result


def test_callbacks_before_eof_no_duplicates_and_child_continues_during_review(pipeline, tmp_path):
    snapshots, candidates = [], []
    output = tmp_path / 'run'
    def review(candidate):
        candidates.append(candidate)
        assert snapshots[-1]['completed'] is False
        # A slow review blocks the parent's polling, never the child scan.
        deadline = time.monotonic()+5
        while True:
            assert time.monotonic() < deadline
            try:
                completed = json.loads((output/'safety/scan.json').read_text(encoding='utf8'))['completed']
            except PermissionError:
                # The test observer can hit the same Windows replace sharing
                # boundary as production polling; keep the bounded deadline.
                time.sleep(.02)
                continue
            if completed:
                break
            time.sleep(.05)
    result = pipeline.scan_video({'original_path':str(pipeline.source)},output,
        on_progress=snapshots.append,on_candidate=review)
    assert len(result) == len(candidates) == 1
    assert snapshots[-1]['completed'] and snapshots[-1]['progress'] == 100
    assert snapshots[-1]['candidate_count'] == 1
    # A cache hit replays once, with no process/model invocation.
    replay, cache = [], []
    result2 = pipeline.scan_video({'original_path':str(pipeline.source)},output,
        on_progress=cache.append,on_candidate=replay.append)
    assert result2 == result == replay
    assert len(cache)==1 and cache[0]['cached'] is True


def test_budget_pause_resume_reuses_review_key_and_keeps_frame_references(pipeline, tmp_path):
    output = tmp_path / 'run'
    paid_reviews = {}
    paid_calls = []
    def review(candidate):
        if candidate['id'] not in paid_reviews:
            paid_calls.append(candidate['id'])
            paid_reviews[candidate['id']]=candidate
            raise AppError('REQUEST_LIMIT','synthetic budget pause',429)
        assert paid_reviews[candidate['id']] == candidate
    with pytest.raises(AppError) as error:
        pipeline.scan_video({'original_path':str(pipeline.source)},output,on_candidate=review)
    assert error.value.code=='REQUEST_LIMIT'
    first=next(iter(paid_reviews.values()))
    assert all(Path(f['path']).is_file() for f in first['frames'])
    result=pipeline.scan_video({'original_path':str(pipeline.source)},output,on_candidate=review)
    assert result==[first] and len(paid_calls)==1
    assert not list(output.glob('safety-interrupted-*'))


def test_control_pause_is_checked_before_cache_or_process(pipeline, tmp_path, monkeypatch):
    def pause():
        raise AppError('JOB_PAUSED','synthetic pause',409)
    def never(*args,**kwargs):
        pytest.fail('paused job spawned subprocess')
    monkeypatch.setattr(hybrid.subprocess,'Popen',never)
    with pytest.raises(AppError) as error:
        pipeline.scan_video({'original_path':str(pipeline.source)},tmp_path/'run',check_control=pause)
    assert error.value.code=='JOB_PAUSED'


def test_signature_changes_when_sampling_scanner_changes(pipeline):
    before=pipeline.signature()
    script=hybrid.ROOT/'elder-safety/scripts/scan-safety-video.py'
    script.write_text(STUB+'\n# different sampling policy\n',encoding='utf-8')
    assert pipeline.signature()!=before


def test_temporary_windows_snapshot_sharing_denial_is_retried(pipeline, tmp_path, monkeypatch):
    original = Path.read_bytes
    failures = []
    def read(path):
        if path.name == 'scan.json' and not failures:
            failures.append(True)
            raise PermissionError('synthetic Windows sharing violation')
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', read)
    candidates = []
    result = pipeline.scan_video({'original_path': str(pipeline.source)}, tmp_path / 'run', on_candidate=candidates.append)
    assert failures and result == candidates and len(result) == 1


def test_local_scan_cache_does_not_depend_on_cloud_model(pipeline, tmp_path, monkeypatch):
    run = tmp_path / 'run'
    first = pipeline.scan_video({'original_path': str(pipeline.source)}, run)
    pipeline.model = 'different-cloud-model-with-no-network'
    def never(*args, **kwargs):
        pytest.fail('cloud reviewer change reloaded local models')
    monkeypatch.setattr(hybrid.subprocess, 'Popen', never)
    assert pipeline.scan_video({'original_path': str(pipeline.source)}, run) == first
