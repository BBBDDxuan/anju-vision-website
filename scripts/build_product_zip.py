"""Curate a portable product ZIP with public samples; never copy private runtime data."""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile

from dotenv import dotenv_values, set_key

from backend.config import ROOT, Settings
from backend.db import Database
from backend.events import validate_memory


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def write(path,text):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(text,encoding='utf8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--include-api-key',action='store_true',help='Explicitly create an additional local private ZIP containing the owner key in .env.')
    args=parser.parse_args()
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')
    package = ROOT/'deliverables'/f'AnjuVision_{stamp}'
    package.mkdir(parents=True,exist_ok=False)
    archive = ROOT/f'安居视界_产品包_{stamp}.zip'
    private = dotenv_values(ROOT/'.env')
    secrets = {value.encode() for key,value in private.items() if value and len(value)>=8
               and any(tag in key.upper() for tag in ('API_KEY','TOKEN','SECRET','PASSWORD'))}
    for directory in ('backend','schemas','prompts','tests'):
        shutil.copytree(ROOT/directory,package/directory,
                        ignore=shutil.ignore_patterns('__pycache__','*.pyc','.pytest_cache','*.log'))
    for relative in ('frontend/src','frontend/dist'):
        shutil.copytree(ROOT/relative,package/relative)
    for name in ('package.json','package-lock.json','tsconfig.json','vite.config.ts','index.html'):
        shutil.copy2(ROOT/'frontend'/name,package/'frontend'/name)
    for name in ('start.ps1','start.bat','requirements.txt','requirements-dev.txt','requirements.lock.txt',
                 'pyproject.toml','.gitignore'):
        shutil.copy2(ROOT/name,package/name)
    (package/'scripts').mkdir()
    shutil.copy2(ROOT/'scripts/import_demo.py',package/'scripts/import_demo.py')
    template = (ROOT/'.env.example').read_text(encoding='utf8')
    for key,value in {'MAX_MODEL_CALLS_PER_DAY':'60','MAX_MODEL_CALLS_PER_VIDEO':'16',
                      'MAX_FRAME_REFERENCES':'256','MAX_OUTPUT_TOKENS_PER_VIDEO':'32768'}.items():
        template,count = re.subn(r'(?m)^'+key+r'=[^\r\n]*',key+'='+value,template)
        assert count == 1
    assert not dotenv_values(stream=__import__('io').StringIO(template)).get('VLM_API_KEY')
    write(package/'.env.example',template)
    docs = ['PRODUCT_USER_GUIDE','ACTIVITY_FILTER','CONFIDENCE_DISPLAY','CONTINUOUS_ANALYSIS',
            'ENVIRONMENT_ALERTS','PERSON_BEHAVIOR_ALERTS','EVENT_DEDUPLICATION',
            'FALL_VALIDATION_REPORT_20261020','DATASET_ACCESS_REQUEST']
    for name in docs:
        text = (ROOT/'docs'/f'{name}.md').read_text(encoding='utf8')
        if name == 'FALL_VALIDATION_REPORT_20261020':
            text = text.replace('data/validation/fall-suite-20261020/','validation/fall-suite-20261020/')
            text = re.sub(r'\[S(\d+)\]\(http://127\.0\.0\.1:8000/workspace\?video=[^)]+\)',r'S\1',text)
            text = '\n'.join('复现说明：本包提供历史验证结果，若需重新识别，请按使用指南上传样本；开发工作区的付费跑批脚本不随包自动运行。' if line.startswith('复现：') else line for line in text.splitlines())
            text += '\n\n交付包说明：表格来自历史测试，只自动导入S02演示。其余样本可配置Key后自行上传分析。\n'
        write(package/'docs'/f'{name}.md',text)
    for name in ('ARCHITECTURE','MVP_SCOPE'):
        write(package/'docs/history'/f'{name}.md',
              '> 历史设计快照。当前操作、预算和验证边界以 docs/PRODUCT_USER_GUIDE.md 为准。\n\n'
              +(ROOT/'docs'/f'{name}.md').read_text(encoding='utf8'))
    shutil.copytree(ROOT/'video/public_samples',package/'video/public_samples',
                    ignore=shutil.ignore_patterns('*.part','*.partial.*','__pycache__'))
    for name in ('火光.mp4','火光_可上传.mp4'):
        shutil.copy2(ROOT/'video'/name,package/'video'/name)
    write(package/'video/DATA_SOURCES.md','''# 数据与测试素材

- CAUCAFall v5：10位参与者的10段模拟跌倒，原始AVI和转换MP4；仅选取这10段，并非完整数据集。许可CC BY 4.0。作者Jose Camilo Eraso、Elena Muñoz、Mariela Muñoz、Jesus Pinto；DOI 10.17632/7w7fccy7ky.5；https://data.mendeley.com/datasets/7w7fccy7ky/5 。转换和来源见public_samples中的清单与provenance。
- 火光.mp4：用户提供的本地测试素材，原始来源及再分发许可未核实；火光_可上传.mp4为保留音轨的H.264转换版。它们不是CAUCAFall，不套用其许可。原片与可上传版分别保留。
- 未纳入早期私人居家录像，未下载Toyota等需授权长视频，未包含模型权重或完整数据集逐帧标签。

CAUCAFall均为表演的模拟跌倒，不应对外称为真实老人意外跌倒。若公开分发或赛事展示，火光素材的使用范围需由提供者确认。
''')
    validation = ROOT/'data/validation/fall-suite-20261020'
    target = package/'validation/fall-suite-20261020'
    target.mkdir(parents=True)
    for file in validation.iterdir():
        if file.is_file() and file.suffix in {'.json','.csv','.jpg','.png'}:
            if file.name == 'suite.json':
                data = json.loads(file.read_text(encoding='utf8'))
                data.pop('existing_raw_hashes',None)
                write(target/file.name,json.dumps(data,ensure_ascii=False,indent=2))
            else:
                shutil.copy2(file,target/file.name)
    (package/'validation/checks').mkdir()
    for name in ('s08-format-retry-tests.xml','s08-real-format-recovery.json',
                 'monitor-grouping-real.json','fire-upload-compatible.json','dedup-tests.xml'):
        path = ROOT/'evidence'/name
        if path.exists():
            shutil.copy2(path,package/'validation/checks'/name)
    # A public, completed, genuinely observed S02 recording, not a mock fixture.
    db = Database(Settings.from_env().data_dir)
    dataset = json.loads((ROOT/'video/public_samples/CAUCAFall_validation_9/MANIFEST.json').read_text(encoding='utf8'))
    source = next(c for c in dataset['clips'] if c['subject'] == 2)
    video = next(v for v in db.list_videos() if v['sha256'] == source['mp4']['sha256'] and v['active_run'])
    memory,job = db.raw_memory(video['id'])
    validate_memory(memory)
    assert job['status']=='completed' and not job['is_mock'] and memory['fixture_kind']=='model_analysis'
    demo = package/'demo-recording'
    (demo/'frames').mkdir(parents=True)
    write(demo/'memory.json',json.dumps(memory,ensure_ascii=False,indent=2))
    write(demo/'job.json',json.dumps(job,ensure_ascii=False,indent=2))
    clip = ROOT/'video/public_samples/CAUCAFall_validation_9'/source['filename']
    for filename in ('video.mp4','playback.mp4'):
        shutil.copy2(clip,demo/filename)
    for frame in memory['frames']:
        shutil.copy2(db.path.parent/'runs'/job['id']/'frames'/(frame['id']+'.jpg'),demo/'frames'/(frame['id']+'.jpg'))
    manifest = {'display_name':'已保存真实模型分析 · CAUCAFall S02.mp4',
                'source_mapping':{'dataset':'CAUCAFall v5','subject':2,'action':'Fall backwards',
                                  'offset_ms':0,'license':'CC BY 4.0','dataset_url':'https://data.mendeley.com/datasets/7w7fccy7ky/5'},
                'sha256':{str(p.relative_to(demo)).replace('\\','/'):sha(p) for p in demo.rglob('*') if p.is_file()}}
    write(demo/'manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    write(demo/'README.md','这是一份已保存的真实Qwen分析，来自CAUCAFall S02模拟跌倒。启动时离线导入，不调用模型，不代表新识别。与合成Mock测试数据区分。数据许可和来源见manifest.json及video/DATA_SOURCES.md。\n')
    notices = ['# 第三方组件和素材说明\n\n前端预构建包含React、React DOM和Scheduler的MIT许可代码，原文如下。Python依赖、FFmpeg、模型权重、Node依赖环境和字体二进制不随包分发。API需使用者自行配置。\n\nCAUCAFall按CC BY 4.0归属原作者，火光样例来源许可未核实，见video/DATA_SOURCES.md。原创产品代码未擅自授予额外开源许可证。\n']
    for name in ('react','react-dom','scheduler'):
        notices.append('\n## '+name+'\n\n'+(ROOT/'frontend/node_modules'/name/'LICENSE').read_text(encoding='utf8'))
    write(package/'THIRD_PARTY_NOTICES.md','\n'.join(notices))
    write(package/'README.md','''# 安居视界 · 产品与数据集交付包

2026-10-20打包。请先完整解压，阅读 [安装与使用说明](docs/PRODUCT_USER_GUIDE.md)。

包含产品后端、React前端源码与构建文件、Schema/提示词、自动化测试、10段CAUCAFall样本的原始AVI和MP4、火光原片与可上传版、验证报告，以及一份可离线查看的真实历史模型结果。

首次使用：安装Python3.13 → 创建.venv → 安装requirements.txt → 仅在没有.env时复制.env.example → 双击start.bat。页面默认为 http://127.0.0.1:8000 。导入历史演示不需要Key；新视频识别需在本地配置自己的Key并明确开启网络。

配置模板每日60次、每批16次，密钥为空、网络默认关闭。包内不含API Key、私人居家录像、生产数据库、Python环境、node_modules、FFmpeg二进制或模型权重。前端已构建，仅修改前端才需要Node/npm。

数据说明见 [video/DATA_SOURCES.md](video/DATA_SOURCES.md)。CAUCAFall为10条选取样本，不是全量数据集；火光文件不属于该数据集，其对外素材许可需确认。已知识别/问答问题见 [实际验证报告](docs/FALL_VALIDATION_REPORT_20261020.md)，不要把10条fall候选等同于100%正确率。

`demo-recording/` 是已保存的真实Qwen结果，不是Mock。`validation/` 中保留测试结果和证据索引；历史报告的调用数反映各轮测试，不是压缩包启动时的费用。

文件校验见MANIFEST_SHA256.json。未公开发布、未上传赛事平台。
''')
    # Verify source sample hashes and absence of credentials/runtime baggage.
    for clip in dataset['clips']:
        assert sha(package/'video/public_samples/CAUCAFall_validation_9'/clip['filename']) == clip['mp4']['sha256']
        assert sha(package/'video/public_samples/CAUCAFall_validation_9/originals'/clip['original_filename']) == clip['provider_sha256']
    first = json.loads((package/'video/public_samples/CAUCAFall_Subject1_FallForward/PROVENANCE.json').read_text(encoding='utf8'))
    for suffix,key in (('.avi','original'),('.mp4','mp4')):
        p = next((package/'video/public_samples/CAUCAFall_Subject1_FallForward').glob('*'+suffix))
        assert sha(p)==first['verification'][key]['sha256']
    files = sorted(p for p in package.rglob('*') if p.is_file())
    for path in files:
        relative = path.relative_to(package)
        assert path.name != '.env' and not ({'.git','node_modules','__pycache__','.venv'} & set(relative.parts))
        assert path.suffix not in {'.sqlite3','.pyc','.log','.private'}
        data = path.read_bytes()
        assert not any(secret in data for secret in secrets), 'Known credential found in '+str(relative)
    hashes = {p.relative_to(package).as_posix():sha(p) for p in files}
    write(package/'MANIFEST_SHA256.json',json.dumps(hashes,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for path in sorted(p for p in package.rglob('*') if p.is_file()):
            z.write(path,package.name+'/'+path.relative_to(package).as_posix())
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        assert len(z.namelist())==len(hashes)+1
        for name,expected in hashes.items():
            assert hashlib.sha256(z.read(package.name+'/'+name)).hexdigest()==expected
    archive_hash = sha(archive)
    write(archive.with_suffix('.zip.sha256'),archive_hash+'  '+archive.name+'\n')
    result = {'archive':str(archive),'package':str(package),'archive_sha256':archive_hash,
              'files':len(hashes)+1,'bytes':archive.stat().st_size,'credential_scan':'passed',
              'zip_crc_and_all_file_hashes':'passed','public_fall_samples':10,'fire_source_and_mp4':True,
              'new_model_calls':0,'extracted_startup_check':'pending'}
    if args.include_api_key:
        key=private.get('VLM_API_KEY') or private.get('DASHSCOPE_API_KEY')
        assert key, 'No locally configured key to include'
        private_package=package.with_name(package.name+'_PRIVATE')
        shutil.copytree(package,private_package)
        shutil.copy2(private_package/'.env.example',private_package/'.env')
        set_key(str(private_package/'.env'),'VLM_API_KEY',key,quote_mode='always')
        for name in ('VLM_BASE_URL','VLM_MODEL','VLM_REVIEW_MODEL'):
            if private.get(name):
                set_key(str(private_package/'.env'),name,private[name],quote_mode='always')
        # Credentials are included by explicit request. Startup/demo still must
        # not initiate paid network traffic; users enable new analysis locally.
        set_key(str(private_package/'.env'),'VLM_ENABLE_NETWORK','false',quote_mode='never')
        assert dotenv_values(private_package/'.env')['VLM_API_KEY']==key
        readme=(private_package/'README.md').read_text(encoding='utf8')
        readme=readme.replace('# 安居视界 · 产品与数据集交付包','# 安居视界 · 含API Key私用交付包')
        readme=readme.replace('新视频识别需在本地配置自己的Key并明确开启网络','本私用版已附Key，新视频识别前在.env将VLM_ENABLE_NETWORK设为true')
        readme=readme.replace('包内不含API Key、私人居家录像','本私用版按用户要求在.env包含本机API Key；包内不含私人居家录像')
        readme+='\n\n私用说明：.env已配置真实Key，但网络默认关闭；不要用.env.example覆盖它。每日额度仅针对本地实例账本，新实例不共享旧机器的调用计数。含Key压缩包未加密，仅私下保存，不要公开上传。对外分享请使用另附的不含Key版本。\n'
        write(private_package/'README.md',readme)
        guide=(private_package/'docs/PRODUCT_USER_GUIDE.md').read_text(encoding='utf8')
        guide=guide.replace('API Key 或模型权重','模型权重；本私用版例外地在.env附上本机API Key')
        guide=guide.replace('## 3. 配置新视频的 Qwen 分析','## 3. 配置新视频的 Qwen 分析\n\n本私用版的.env已经填入Key和对应服务地址。首次识别新视频只需将VLM_ENABLE_NETWORK改为true后重启；下面填写Key的示例用于更换账号。默认启动和离线演示不调用付费模型。')
        write(private_package/'docs/PRODUCT_USER_GUIDE.md',guide)
        private_files=sorted(p for p in private_package.rglob('*') if p.is_file() and p.name!='MANIFEST_SHA256.json')
        matches=[]
        for path in private_files:
            if any(secret in path.read_bytes() for secret in secrets):
                matches.append(path.relative_to(private_package).as_posix())
        assert matches==['.env'], 'Credential unexpectedly present outside the explicitly requested .env'
        write(private_package/'MANIFEST_SHA256.json',json.dumps({p.relative_to(private_package).as_posix():sha(p) for p in private_files},ensure_ascii=False,indent=2))
        private_zip=ROOT/f'安居视界_私用版_含APIKey_{stamp}.zip'
        with zipfile.ZipFile(private_zip,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            for path in sorted(p for p in private_package.rglob('*') if p.is_file()):
                z.write(path,private_package.name+'/'+path.relative_to(private_package).as_posix())
        with zipfile.ZipFile(private_zip) as z:
            assert z.testzip() is None
            archived_env=z.read(private_package.name+'/.env').decode('utf8')
            assert dotenv_values(stream=__import__('io').StringIO(archived_env))['VLM_API_KEY']==key
            for path in private_files:
                assert hashlib.sha256(z.read(private_package.name+'/'+path.relative_to(private_package).as_posix())).hexdigest()==sha(path)
        private_hash=sha(private_zip)
        write(private_zip.with_suffix('.zip.sha256'),private_hash+'  '+private_zip.name+'\n')
        result['private']={'archive':str(private_zip),'package':str(private_package),'archive_sha256':private_hash,
                           'files':len(private_files)+1,'bytes':private_zip.stat().st_size,'contains_api_key':True,
                           'key_location':'.env','network_enabled_by_default':False,
                           'credential_scan':'expected_key_only_in_.env','extracted_startup_check':'pending'}
    write(ROOT/'evidence/product-package-build.json',json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
