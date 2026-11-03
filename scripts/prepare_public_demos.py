"""Export source-verified public samples and the user's explicitly authorized demonstration clips."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.db import Database
from backend.events import validate_memory


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    public = ROOT/'video/public_samples/CAUCAFall_validation_9'
    source = json.loads((public/'MANIFEST.json').read_text(encoding='utf8'))
    assert source['license'] == 'CC BY 4.0' and source['dataset'] == 'CAUCAFall'
    db = Database(ROOT/'data')
    target = ROOT/'data/public-demo-bundles'/datetime.now().strftime('%Y%m%d_%H%M%S')
    target.mkdir(parents=True, exist_ok=False)
    demos = []
    selections = [
        {'filename':'05_S06_simulated_fall_forward.mp4','title':'黑夜/低照度跌倒 · 已保存真实分析','case_type':'night_fall'},
        {'filename':'09_S10_simulated_fall_sitting.mp4','title':'白天跌倒 · 已保存真实分析','case_type':'day_fall'},
        {'path':ROOT/'video/火光_可上传.mp4','sha':'4aa1d049852c0f91b98d983a2c475650ca0dfc831328696fae48064150509325',
         'title':'火光/明火 · 已保存真实分析','case_type':'fire',
         'description':'用户授权公开的受控打火机录像，已记录3次明火；不等同于电弧火花或真实火灾。'},
        {'path':ROOT/'video/桌面_逆时针90度.mp4','sha':'c87ee0f8a562ad381f5da5296a24b6a22cd5f85b674dbe9b3fc5db39ef877bad',
         'title':'长视频 · 6分34秒 · 已保存真实分析','case_type':'long_video',
         'description':'用户授权公开的约7分钟真实桌面录像，查看物品活动、踪迹回溯与记录概括。',
         'playback':ROOT/'data/public-desktop-playback.mp4'},
    ]
    for spec in selections:
        title = spec['title']
        if 'filename' in spec:
            provenance = json.loads((public/Path(spec['filename']).with_suffix('.provenance.json')).read_text(encoding='utf8'))
            sha = provenance['mp4']['sha256']
            assert digest(public/spec['filename']) == sha
            mapping = {'dataset':'CAUCAFall v5','subject':provenance['subject'],'action':provenance['action'],
                       'offset_ms':0,'license':'CC BY 4.0','dataset_url':source['source'],'authors':source['authors'],
                       'original_filename':provenance['original_filename'],'original_sha256':provenance['original']['sha256'],
                       'mp4_sha256':sha,'modification':'AVI converted to MP4; adult simulated fall, not an elderly accident.'}
        else:
            sha = spec['sha']; assert digest(spec['path']) == sha
            mapping = {'dataset':'User-provided demonstration','license':'User-authorized public demonstration',
                       'authorization_date':'2026-10-21','mp4_sha256':sha,'source_filename':spec['path'].name,
                       'notice':'Permission for this project demonstration; no additional dataset license is granted.'}
        matches = [v for v in db.list_videos() if v['sha256'] == sha and v['active_run']]
        assert len(matches) == 1
        video = db.video(matches[0]['id'])
        document, job = db.memory(video['id'])
        validate_memory(document)
        assert document['fixture_kind'] == 'model_analysis' and job['status'] == 'completed' and not job['is_mock']
        assert document['model_run']['input_video_sha256'] == sha == digest(Path(video['original_path']))
        folder = target/video['id']; (folder/'frames').mkdir(parents=True)
        derivative = None
        if spec.get('playback'):
            # Original model-input hash remains unchanged; only the public playback is compressed.
            probe = 'C:/Users/Administrator/AppData/Local/Programs/Python/Python313/Scripts/ffprobe.EXE'
            def timestamps(path):
                r = subprocess.run([probe,'-v','error','-select_streams','v:0','-show_entries',
                    'frame=best_effort_timestamp_time,pkt_pts_time','-of','json',str(path)],capture_output=True,check=True)
                return [float(f.get('best_effort_timestamp_time') or f['pkt_pts_time']) for f in json.loads(r.stdout)['frames']]
            original_times, playback_times = timestamps(spec['path']), timestamps(spec['playback'])
            assert len(original_times)==len(playback_times) and all(abs(a-b)<.001 for a,b in zip(original_times,playback_times))
            shutil.copy2(spec['playback'],folder/'playback.mp4')
            derivative = {'original_input_sha256':sha,'playback_sha256':digest(spec['playback']),
                          'transformation':'H.264 640px-wide CRF28 playback copy; original frame timestamps retained.',
                          'frame_timestamps_verified':True,'frames':len(original_times)}
        else:
            shutil.copy2(video['original_path'], folder/'video.mp4')
            shutil.copy2(video['playback_path'], folder/'playback.mp4')
        # Export stored effective records, not the provider's credential-bearing transport logs.
        (folder/'memory.json').write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf8')
        (folder/'job.json').write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding='utf8')
        for frame in document['frames']:
            assert frame['id'].replace('_','').replace('-','').isalnum()
            shutil.copy2(ROOT/'data/runs'/job['id']/'frames'/(frame['id']+'.jpg'), folder/'frames'/(frame['id']+'.jpg'))
        (folder/'README.md').write_text(json.dumps(mapping,ensure_ascii=False,indent=2)+
            '\nSaved genuine model output. No new model calls during export.\n', encoding='utf8')
        focus = next((e for e in document['events'] if e['action'] in {'flame','fall'}),None)
        poster = min(document['frames'],key=lambda f:abs(f['pts_ms']-((focus['start_ms']+focus['end_ms'])/2 if focus else 0)))['id']
        manifest = {'display_name':title+'.mp4', 'demo_description':spec.get('description','公开成年人模拟跌倒样例，支持重点事件回看和证据问答。'),
                    'case_type':spec['case_type'],'poster_frame_id':poster,'playback_derivative':derivative,
                    'source_mapping':mapping, 'sha256':{p.relative_to(folder).as_posix():digest(p) for p in folder.rglob('*') if p.is_file()}}
        (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
        demos.append({'folder':video['id'], 'video_id':video['id'], 'input_sha256':sha})
    report = {'directory':str(target), 'demos':demos, 'new_model_calls':0, 'licenses':['CC BY 4.0','User-authorized public demonstration']}
    (ROOT/'data/public-demo-catalog.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__':
    main()
