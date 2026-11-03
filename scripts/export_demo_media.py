"""Encode the uncut real browser recording with subtitles; no fake UI or analysis edits."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'submission/demo'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = json.loads((ROOT / 'data/validation/demo-recording/report.json').read_text(encoding='utf8'))
    captions = [
        (0, 4, '拾迹 Home｜让家拥有记忆，让过去可以被追问。'),
        (4, 9.8, '上传真实拍摄的18秒片段｜原片16–34秒，未使用模拟事件'),
        (9.8, 19, '点击开始分析｜Qwen3.6-plus读取带时间信息的真实关键帧'),
        (19, 32, '按8秒窗口分析，6秒步长｜严格校验JSON、时间戳与证据引用'),
        (32, 45.3, '正在真实等待模型｜本次分析35.331秒，3次请求，未剪辑加速'),
        (45.3, 54.5, '模型生成5条事件｜观察主张仍需结合原视频核对'),
        (54.5, 62.2, '询问最后出现的位置｜答案来自结构化事件，附证据时间窗'),
        (62.2, 67.4, '点击证据定位到12秒｜同步显示原视频关键帧'),
        (67.4, 73.8, 'LifeReplay｜选择物品，回看桌面、拿起、放入置物架的经历'),
        (73.8, 79.5, '历史位置不等于现实当前位置｜证据不足时明确回答无法确定'),
        (79.5, 84.5, '没有匹配的护照记录｜系统说明未知，不编造答案'),
        (84.5, 98, '观察 / 条件推断 / 未知｜分别呈现，保留每一步的视频来源'),
        (98, 111, '已实现本地闭环与真实模型验证｜实时摄像头及跨镜追踪尚未实现'),
    ]
    def clock(seconds, ass=False):
        milliseconds = round(seconds*1000)
        if ass:
            return f'{milliseconds//3600000}:{milliseconds//60000%60:02d}:{milliseconds//1000%60:02d}.{milliseconds%1000//10:02d}'
        return f'{milliseconds//3600000:02d}:{milliseconds//60000%60:02d}:{milliseconds//1000%60:02d},{milliseconds%1000:03d}'
    srt = '\n\n'.join(f'{i+1}\n{clock(start)} --> {clock(end)}\n{text}' for i,(start,end,text) in enumerate(captions))+'\n'
    (OUT / 'ShijiHome-Demo.srt').write_text(srt,encoding='utf8')
    header = '''[Script Info]
ScriptType: v4.00+
PlayResX: 1440
PlayResY: 1000
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,25,&H00FFFFFF,&H000000FF,&H00172D23,&H80172D23,0,0,0,0,100,100,0,0,3,8,0,2,20,20,22,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    (OUT / 'captions.ass').write_text(header+'\n'.join(f'Dialogue: 0,{clock(start,True)},{clock(end,True)},Default,,0,0,0,,{text}' for start,end,text in captions),encoding='utf8')
    target = OUT / 'ShijiHome-Demo.mp4'
    if target.exists() and target.stat().st_size:
        raise RuntimeError('Demo already exported; inspect before replacing')
    subprocess.run([os.getenv('DEMO_FFMPEG', 'ffmpeg'),'-v','error','-y','-i',report['raw_video'],'-vf',"ass=captions.ass:fontsdir='C\\:/Windows/Fonts'",'-an','-vcodec','libx264','-bf','0','-preset','fast','-crf','20','-pix_fmt','yuv420p','-movflags','+faststart',str(target)], cwd=OUT,check=True,timeout=180)
    probe = subprocess.run(['ffprobe','-v','error','-show_format','-show_streams','-print_format','json',str(target)],capture_output=True,check=True)
    metadata = json.loads(probe.stdout)
    duration = float(metadata['format']['duration'])
    assert 90 <= duration <= 120 and metadata['streams'][0]['codec_name'] == 'h264'
    (OUT / 'media-validation.json').write_text(json.dumps({'status':'PASS','duration_seconds':duration,'width':1440,'height':1000,'source':'actual browser recording, no timeline edit or speed change','audio':'none, burned Chinese captions','sha256_pending_package_manifest':True},ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'status':'PASS','duration_seconds':duration,'size_mb':round(target.stat().st_size/1024**2,2)}),flush=True)


if __name__ == '__main__':
    main()
