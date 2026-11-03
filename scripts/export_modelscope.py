"""Export an allowlisted deployment directory; never copy local credentials/runtime data."""
from datetime import datetime
import argparse
import hashlib
import json
import re
from pathlib import Path
import shutil
import zipfile

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reuse-runtime-image', default='', help='Exact digest of this project\'s previously built ModelScope runtime')
    args = parser.parse_args()
    target = ROOT/'deliverables'/('ModelScope_AnjuVision_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
    target.mkdir(parents=True, exist_ok=False)
    for name in ('Dockerfile', '.dockerignore', 'ms_deploy.json', 'requirements.txt'):
        shutil.copy2(ROOT/name, target/name)
    docker_allowlist = (target/'.dockerignore').read_text(encoding='utf8')
    assert all(rule in docker_allowlist.splitlines() for rule in ['!demo-recordings/','!demo-recordings/**','!demo-catalog.json'])
    if args.reuse_runtime_image:
        if not re.fullmatch(r'mshub-registry\.cn-zhangjiakou\.cr\.aliyuncs\.com/modelscope-repo/independent-service@sha256:[a-f0-9]{64}', args.reuse_runtime_image):
            raise ValueError('Reuse requires an exact digest in the verified ModelScope runtime repository.')
        shutil.copy2(ROOT/'Dockerfile', target/'Dockerfile.full')
        text = (target/'Dockerfile').read_text(encoding='utf8')
        text = text.replace('FROM python:3.13-slim-bookworm', 'FROM '+args.reuse_runtime_image)
        (target/'Dockerfile').write_text(text, encoding='utf8')
    for name in ('backend', 'schemas', 'prompts', 'frontend/src'):
        shutil.copytree(ROOT/name, target/name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.log'))
    for name in ('package.json', 'package-lock.json', 'tsconfig.json', 'vite.config.ts', 'index.html'):
        shutil.copy2(ROOT/'frontend'/name, target/'frontend'/name)
    (target/'scripts').mkdir()
    for name in ('cloud_entrypoint.py', 'import_demo.py'):
        shutil.copy2(ROOT/'scripts'/name, target/'scripts'/name)
    demo = ROOT/'deliverables/AnjuVision_20261020_221231/demo-recording'
    shutil.copytree(demo, target/'demo-recording')
    legacy_manifest = target/'demo-recording/manifest.json'
    legacy_data = json.loads(legacy_manifest.read_text(encoding='utf8'))
    legacy_data['demo_gallery_visible'] = False
    legacy_manifest.write_text(json.dumps(legacy_data,ensure_ascii=False,indent=2),encoding='utf8')
    public_catalog = json.loads((ROOT/'data/public-demo-catalog.json').read_text(encoding='utf8'))
    catalog = {'demos':[]}
    public_root = Path(public_catalog['directory']).resolve()
    assert public_root.is_relative_to((ROOT/'data/public-demo-bundles').resolve())
    for item in public_catalog['demos']:
        assert re.fullmatch(r'v_[a-f0-9]+', item['folder'])
        folder = public_root/item['folder']
        metadata = json.loads((folder/'manifest.json').read_text(encoding='utf8'))
        if metadata['source_mapping']['license'] != 'CC BY 4.0':
            assert metadata['source_mapping']['license'] == 'User-authorized public demonstration'
            assert item['input_sha256'] in {
                '4aa1d049852c0f91b98d983a2c475650ca0dfc831328696fae48064150509325',
                'c87ee0f8a562ad381f5da5296a24b6a22cd5f85b674dbe9b3fc5db39ef877bad'}
        assert metadata['source_mapping']['mp4_sha256'] == item['input_sha256']
        relative = 'demo-recordings/'+item['folder']
        shutil.copytree(folder, target/relative)
        catalog['demos'].append({'directory':relative, 'video_id':item['video_id']})
    (target/'demo-catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2),encoding='utf8')
    (target/'deployment').mkdir()
    shutil.copy2(ROOT/'deployment/modelscope.env.example', target/'deployment/modelscope.env.example')
    (target/'assets').mkdir()
    shutil.copy2(ROOT/'submission/assets/安居视界_作品封面_1920x1080.png', target/'assets/cover.png')
    (target/'.gitignore').write_text('.env\n.env.*\n__pycache__/\n*.pyc\nnode_modules/\ndata/\n*.log\n', encoding='utf8')
    # Hash-verified exported recordings must retain byte-identical JSON across
    # Windows Git and Linux builds; normal source files can use LF consistently.
    (target/'.gitattributes').write_text('* text=auto eol=lf\ndemo-recording/** -text\ndemo-recordings/** -text\n*.mp4 -text\n*.png -text\n*.jpg -text\n', encoding='utf8')
    (target/'README.md').write_text('''# 安居视界

面向独居老人关怀与家庭危险识别的AI居家录像分析原型。当前支持上传录像分析，尚未接入实时摄像头或手机推送。

## 在线体验

打开应用首页可直接选择4类案例：火光/明火、白天跌倒、黑夜/低照度跌倒、约7分钟桌面长录像，已内置真实Qwen分析，无需上传或等待识别。可回放、查看红色候选提醒和证据问答。点击“重新分析此案例”会创建当前访客的独立副本并重新调用视觉模型；不复用旧分析，原展示成果保留。长案例用云端压缩视频重新识别，新结果记录实际输入哈希。副本计入访客录像容量，支持暂停、继续与删除，新分析计入每日100次请求。回放不调用新模型；开启“大模型解读”后问答最多新增1次文本模型请求，计入每日上限。人数统计、重点事件检索、缓存命中和关闭模型后的本地检索不新增请求。

也可上传自己的MP4（应用不设文件大小与时长上限，实际受平台网关、超时及可用存储限制），点击“开始 AI 分析”，获得新的真实模型结果。访客上传通过浏览器会话隔离，每位访客最多保留3段录像；请勿上传敏感画面。若内嵌页无法保留Cookie，请通过平台的独立页面入口体验。删除自己的录像可释放空间；清除浏览器Cookie会丢失访问该访客录像的凭据。

## 部署

选择Docker与账号当前可用的免费CPU资源。模型在后端调用远程API，无需GPU。端口7860，单进程运行。根据deployment/modelscope.env.example设置变量，把VLM_API_KEY单独放入平台Secrets。VLM_BASE_URL应与Key对应的服务商匹配，不能保留示例域名。只有配置好后启用VLM_ENABLE_NETWORK=true。

MAX_UPLOAD_MB=0、MAX_VIDEO_SECONDS=0取消应用层大小与时长上限；CLOUD_STORAGE_MB=0取消固定演示存储配额，仍检查可用磁盘。云端超过16MiB的视频自动按4MiB分片传输，在服务端合并校验，避免魔搭单次请求32MiB的限制；原视频字节不变。上传中可取消，未完成上传在闲置30分钟后的下一次上传或服务重启时清理。每日全站最多100次模型请求，含失败及重试；并发1，额度耗尽时暂停。免费托管不免除模型API费用。存储使用/mnt/workspace/anju-data，平台迁移或重命名等情况仍需备份。

源码不含本机.env、API Key、私人居家录像或本机运行数据库。Docker构建和云端网络访问以实际部署日志与验证结果为准。

## 数据来源与边界

跌倒样例来自CAUCAFall v5，首页包含S06低照度、S10白天坐姿模拟跌倒，历史S02样例保留原链接兼容。它们不是实际老人事故。数据作者：Jose Camilo Eraso、Elena Muñoz、Mariela Muñoz、Jesus Pinto；许可CC BY 4.0；来源https://data.mendeley.com/datasets/7w7fccy7ky/5 ，DOI 10.17632/7w7fccy7ky.5。AVI转换为MP4，来源与哈希见各manifest.json。

火光与桌面长录像由用户于2026-10-21明确指定用于公开案例，不套用CAUCAFall许可。火光实际记录3次打火机明火，不能据此宣称电弧火花检测已经验证。长录像实际约6分34秒，云端回放使用压缩副本，逐帧时间戳经核对保留；原始模型输入SHA256不改写，原始高清文件不随云端包分发。Toyota授权数据不在公开包内。

AI生成的项目封面是概念插画，不是实际监控或识别截图。风险提醒不等于已确认事故；颜色不代表准确率，未检出不表示没有风险。本作品不能替代消防探测器、实时照护或紧急呼救。
''', encoding='utf8')
    if args.reuse_runtime_image:
        with (target/'README.md').open('a', encoding='utf8') as readme:
            readme.write('\n## 构建缓存说明\n\n当前Dockerfile复用本项目在魔搭构建的固定摘要依赖镜像，以避免反复下载FFmpeg。完整独立构建方式保留在Dockerfile.full；迁移至其他平台时可将其复制为Dockerfile。镜像不包含运行时Secrets。\n')
    # Verify source sample hashes before considering the directory deployable.
    for folder in [target/'demo-recording', *[target/item['directory'] for item in catalog['demos']]]:
        manifest = json.loads((folder/'manifest.json').read_text(encoding='utf8'))
        for relative, expected in manifest['sha256'].items():
            path = (folder/relative).resolve()
            assert path.is_relative_to(folder.resolve())
            assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
    secrets = {value.encode() for key, value in dotenv_values(ROOT/'.env').items()
               if value and len(value) >= 8 and any(s in key.upper() for s in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD'))}
    files = [p for p in target.rglob('*') if p.is_file()]
    for path in files:
        assert path.name != '.env' and path.suffix not in {'.sqlite3', '.zip'}
        content = path.read_bytes()
        assert not any(secret in content for secret in secrets), 'Credential found in export; refuse deployment'
        assert len(content) < 100*1024**2, 'Unexpected large file'
    archive = target.with_suffix('.zip')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as z:
        for path in files:
            z.write(path, path.relative_to(target).as_posix())
    report = {'directory': str(target), 'archive': str(archive), 'files': len(files),
              'bytes': archive.stat().st_size, 'contains_credentials': False,
              'sample_hashes_verified': True, 'docker_build_verified': False,
              'public_demo_count':len(catalog['demos']),
              'reused_runtime_image': args.reuse_runtime_image or None}
    (ROOT/'data/modelscope-export.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
