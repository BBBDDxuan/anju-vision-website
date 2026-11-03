"""Render code-based voting layouts with the existing cover and verified QR.

No new AI image/model calls, no voting, and no external publication.
"""
import base64
from datetime import datetime
import html
import json
from pathlib import Path
import shutil
import zipfile

import cv2
import numpy as np
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
VOTE='https://pages.tmall.com/wow/a/act/tmall/dailygroup/24732/25590/wupr?spm=a21wqg.46886397.0.0.333ef173se6igz&wh_pid=daily-597100&disableNav=YES&status_bar_transparent=true#works/294001'
DEMO='https://modelscope.cn/studios/BBBDDxuan/anju-vision'
GITHUB='https://github.com/BBBDDxuan/anju-vision'
TITLE='我做了一个AI居家安全小助手，想请你帮它投一票'
SUMMARY='安居视界，把居家录像整理成可追问、可回看的事件线索。面向独居老人关怀与家庭安全，邀请你为这个「智能日常」作品投一票。'


def image_read(path):
    return cv2.imdecode(np.fromfile(path,dtype=np.uint8),cv2.IMREAD_COLOR)


def main():
    out=ROOT/'deliverables'/('安居视界_微信投票素材_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
    out.mkdir(parents=True,exist_ok=False)
    cover=out/'作品封面_1920x1080.png'
    shutil.copy2(ROOT/'submission/assets/安居视界_作品封面_1920x1080.png',cover)
    params=cv2.QRCodeEncoder_Params()
    params.correction_level=cv2.QRCodeEncoder_CORRECT_LEVEL_M
    matrix=cv2.QRCodeEncoder_create(params).encode(VOTE)
    matrix=cv2.copyMakeBorder(matrix,4,4,4,4,cv2.BORDER_CONSTANT,value=255)
    qr=cv2.resize(matrix,None,fx=12,fy=12,interpolation=cv2.INTER_NEAREST)
    qr_path=out/'安居视界_投票二维码.png'
    ok,encoded=cv2.imencode('.png',qr)
    assert ok
    encoded.tofile(qr_path)
    assert cv2.QRCodeDetector().detectAndDecode(qr)[0]==VOTE
    poster=out/'海报排版源文件.html'
    poster.write_text(POSTER.replace('{{COVER}}',cover.as_uri()).replace('{{QR}}',qr_path.as_uri()),encoding='utf8')
    cover_data='data:image/png;base64,'+base64.b64encode(cover.read_bytes()).decode()
    qr_data='data:image/png;base64,'+base64.b64encode(qr_path.read_bytes()).decode()
    article=out/'微信公众号推文_排版稿.html'
    article.write_text(ARTICLE.replace('{{TITLE}}',TITLE).replace('{{COVER}}',cover_data).replace('{{QR}}',qr_data)
        .replace('{{VOTE}}',html.escape(VOTE,quote=True)).replace('{{DEMO}}',DEMO).replace('{{GITHUB}}',GITHUB),encoding='utf8')
    (out/'微信公众号推文_可复制文字.md').write_text(MARKDOWN.replace('{{TITLE}}',TITLE).replace('{{SUMMARY}}',SUMMARY)
        .replace('{{VOTE}}',VOTE).replace('{{DEMO}}',DEMO).replace('{{GITHUB}}',GITHUB),encoding='utf8')
    (out/'朋友圈和微信群_配文.txt').write_text('我参加AI黑客松的作品「安居视界」上线啦！\n\n它把居家录像整理成人物活动、危险线索和可回看的视频证据，面向独居关怀与家庭安全。\n\n想请你长按海报二维码，为我的作品投一票，也欢迎转发支持，谢谢！\n\n投票链接：'+VOTE+'\n\n在线体验：'+DEMO+'\n',encoding='utf8')
    (out/'使用说明.txt').write_text(INSTRUCTIONS.replace('{{VOTE}}',VOTE),encoding='utf8')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1080,'height':1620},device_scale_factor=1)
        page.goto(poster.as_uri(),wait_until='load')
        page.evaluate('document.fonts.ready')
        assert page.evaluate('document.documentElement.scrollWidth===1080 && document.documentElement.scrollHeight===1620')
        assert page.locator('.vote-qr img').evaluate('img=>img.complete&&img.naturalWidth>0')
        page.screenshot(path=str(out/'安居视界_朋友圈投票海报.png'))
        page.screenshot(path=str(out/'安居视界_朋友圈投票海报.jpg'),type='jpeg',quality=96)
        page.set_viewport_size({'width':390,'height':844})
        page.goto(article.as_uri(),wait_until='load')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert page.locator('img').evaluate_all('(imgs)=>imgs.every(img=>img.complete&&img.naturalWidth>0)')
        page.screenshot(path=str(out/'公众号推文_手机预览.png'),full_page=True)
        browser.close()
    checks={}
    for name in ['安居视界_朋友圈投票海报.png','安居视界_朋友圈投票海报.jpg']:
        path=out/name
        pic=image_read(path)
        assert pic.shape[:2]==(1620,1080)
        native=cv2.QRCodeDetector().detectAndDecode(pic)[0]
        half=cv2.QRCodeDetector().detectAndDecode(cv2.resize(pic,(540,810),interpolation=cv2.INTER_AREA))[0]
        assert native==VOTE and half==VOTE, 'Poster QR failed decode: '+name
        assert path.stat().st_size<10*1024**2
        checks[name]={'width':1080,'height':1620,'bytes':path.stat().st_size,'qr_matches':True,'half_size_qr_matches':True}
    checks['standalone_qr']={'width':qr.shape[1],'height':qr.shape[0],'matches':True}
    (out/'制作校验.json').write_text(json.dumps({'vote_url':VOTE,'artifacts':checks,'article_mobile_overflow':False,
        'wechat_publication':'draft_only','live_wechat_scan_tested':False,'paid_model_calls':0},ensure_ascii=False,indent=2),encoding='utf8')
    package=out.with_suffix('.zip')
    with zipfile.ZipFile(package,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in out.iterdir():
            if path.name=='海报排版源文件.html':
                continue  # Local absolute asset paths are not a portable public page.
            archive.write(path,path.name)
    (ROOT/'data/vote-materials.json').write_text(json.dumps({'directory':str(out),'archive':str(package),'qr_verified':True,
        'wechat_article':'draft_only','vote_url':VOTE},ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'directory':str(out),'archive':str(package),'poster':checks,'wechat_article':'draft_only'},ensure_ascii=False))


POSTER='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>安居视界 · 作品投票海报</title>
<style>
*{box-sizing:border-box}html,body{margin:0;width:1080px;height:1620px;overflow:hidden}
body{font-family:"Microsoft YaHei","PingFang SC",sans-serif;color:#123d32;background:#f7f5ed}
.poster{width:1080px;height:1620px;padding:58px 64px 44px;display:flex;flex-direction:column;background:radial-gradient(ellipse at 100% 0%,#e0e9db 0,transparent 49%),#f7f5ed}
.top{display:flex;align-items:center;justify-content:space-between;font-size:27px}.brand{font-size:33px;font-weight:700;letter-spacing:3px}.eyebrow{font-size:23px;border:1px solid #c9d6c7;border-radius:40px;padding:10px 20px;color:#42604d}
h1{font-size:76px;line-height:1.23;letter-spacing:1px;margin:28px 0 14px;font-weight:750} .sub{font-size:30px;letter-spacing:1px;margin:0;color:#486252}
.hero{margin-top:24px}.hero img{display:block;width:920px;height:517.5px;margin:0 auto;object-fit:contain;border-radius:22px;box-shadow:0 16px 44px #143f2e12}.caption{font-size:19px;color:#778275;margin:12px 0 0}
.features{display:flex;gap:13px;margin-top:22px}.features span{border:1px solid #d1dacf;border-radius:11px;padding:10px 20px;background:#fff9;font-size:24px;letter-spacing:1px}
.vote{margin-top:auto;display:flex;align-items:center;gap:28px;padding:24px;background:#174a3c;color:white;border-radius:28px;box-shadow:0 18px 42px #0d392519}
.vote-qr{width:414px;height:414px;flex:none;background:white;border-radius:16px;overflow:hidden}.vote-qr img{width:414px;height:414px;display:block;image-rendering:pixelated}
.vote-copy{flex:1}.mini{color:#cedfc5;font-size:23px;letter-spacing:3px;margin:0 0 13px}.vote h2{font-size:43px;line-height:1.4;margin:0 0 16px;letter-spacing:1px}.action{font-size:28px;line-height:1.7;margin:0}.thanks{font-size:22px;color:#dbe7d8;margin:22px 0 0}
.footer{margin:26px 0 0;text-align:center;font-size:23px;letter-spacing:1px;color:#5d705e}
</style><div class="poster"><div class="top"><span class="brand">安居视界 ANJU VISION</span><span class="eyebrow">AI黑客松 · 智能日常</span></div>
<h1>让居家风险<br>更容易被看见。</h1><p class="sub">独居关怀与家庭危险识别 · 居家录像 AI 分析</p>
<div class="hero"><img src="{{COVER}}"><p class="caption">概念封面示意 · 当前体验基于上传录像分析</p></div>
<div class="features"><span>人物行为线索</span><span>家庭危险候选</span><span>视频证据回看</span></div>
<div class="vote"><div class="vote-qr"><img src="{{QR}}"></div><div class="vote-copy"><p class="mini">邀请你，为作品助力</p><h2>为安居视界<br>投一票</h2><p class="action">长按识别二维码<br>进入作品页支持我</p><p class="thanks">谢谢支持，也欢迎转发</p></div></div>
<p class="footer">把关心家人的想法，做成可以体验的 AI 作品。</p></div></html>'''

ARTICLE='''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{{TITLE}}</title></head>
<body style="margin:0;background:#f3f5f1;color:#293c34;font-family:'Microsoft YaHei','PingFang SC',sans-serif;">
<article style="max-width:720px;margin:0 auto;background:white;padding:30px 24px 45px;box-sizing:border-box;line-height:1.95;font-size:16px;overflow-wrap:anywhere;">
<p style="margin:0 0 14px;color:#6e846f;font-size:13px;letter-spacing:2px;">AI黑客松 · 智能日常 · 作品邀请</p>
<h1 style="font-size:27px;line-height:1.55;margin:0 0 15px;color:#174a3c;">{{TITLE}}</h1>
<p style="margin:0 0 22px;color:#7a867c;font-size:14px;">安居视界｜让需要关注的时刻，更容易被看见。</p>
<img src="{{COVER}}" alt="安居视界作品概念封面" style="display:block;width:100%;height:auto;border-radius:12px;">
<p style="font-size:12px;color:#899185;margin:6px 0 25px;">概念封面示意，当前作品基于上传录像分析。</p>
<p>如果家人独自在家，我们惦记的，往往是一个简单的问题：<strong style="color:#174a3c;">有没有发生需要留意的情况？</strong></p>
<p>摄像头留下了画面，但真正值得关注的几秒，可能藏在很长的录像里。这次参加 AI 黑客松，我做了一个名叫<strong style="color:#174a3c;">「安居视界」</strong>的作品，希望让 AI 帮我们整理这些线索，也让每个判断都能回到视频中核对。</p>
<section style="margin:28px 0;padding:19px 20px;background:#edf4ec;border-left:4px solid #4e7a5f;border-radius:4px;">作品参加的是<strong>「智能日常」</strong>赛道：让 AI 真正进入每一天，而不只待在聊天框。</section>
<h2 style="font-size:21px;color:#174a3c;margin:32px 0 15px;">它能帮我们看什么？</h2>
<h3 style="font-size:17px;color:#345b43;margin:24px 0 8px;">关注家中人的活动</h3>
<p>上传一段录像后，系统整理人物出现、行走、坐下等活动，并将疑似跌倒、求助等重要线索优先呈现。它不识别具体身份，而是围绕画面中的人物和行为组织记录。</p>
<h3 style="font-size:17px;color:#345b43;margin:24px 0 8px;">留意家庭危险线索</h3>
<p>烟雾、明火和火花等候选事件会单独展示。点击提醒，就能回看相关视频片段和关键帧，进一步理解画面里发生了什么。</p>
<h3 style="font-size:17px;color:#345b43;margin:24px 0 8px;">像聊天一样，追问这段录像</h3>
<p>你可以问：“这段录像有哪些需要关注的情况？”“什么时候出现疑似跌倒？”也可以追溯物品最后出现的位置。回答关联已有事件和视频证据，并区分直接观察与上下文推测。</p>
<h2 style="font-size:21px;color:#174a3c;margin:32px 0 15px;">已经做到哪一步？</h2>
<p>目前已完成可在线体验的原型：视频上传、AI 事件提取、事件时间线、证据回看和自然语言问答均已串联。线上可以直接查看火光/明火、白天模拟跌倒、低照度模拟跌倒及桌面长录像的真实分析案例，也能创建自己的副本重新分析。</p>
<p style="font-size:14px;color:#718071;">当前分析的是上传录像，实时摄像头与手机推送还在后续计划中。跌倒案例为模拟行为；识别线索可能漏检或误判，需要结合原片理解，不替代专业探测设备或实时照护。</p>
<section style="margin:34px 0 22px;padding:25px 20px;text-align:center;background:#174a3c;color:white;border-radius:16px;">
<p style="font-size:14px;letter-spacing:3px;margin:0 0 10px;color:#d9e6cf;">想请你，帮作品投一票</p>
<h2 style="font-size:25px;margin:0 0 14px;line-height:1.5;">支持「安居视界」<br>让这个想法被更多人看见</h2>
<p style="margin:0 0 18px;">长按识别下方二维码<br>进入我的作品页，按页面提示投票</p>
<img src="{{QR}}" alt="安居视界作品投票二维码" style="display:block;width:250px;max-width:100%;height:auto;margin:0 auto;border-radius:10px;">
<p style="font-size:14px;margin:18px 0 0;color:#dbe8d8;">如果你也认同这个方向，欢迎转发给朋友。<br>谢谢每一份支持！</p></section>
<p style="text-align:center;"><a href="{{VOTE}}" style="color:#356a4b;font-weight:bold;">作品投票入口</a></p>
<p style="font-size:14px;color:#6d7e6c;border-top:1px solid #e0e7dd;padding-top:20px;margin-top:30px;">也欢迎先体验，再决定是否支持：<br><a href="{{DEMO}}" style="color:#356a4b;">在线体验：魔搭创空间</a><br><a href="{{GITHUB}}" style="color:#356a4b;">项目源码：GitHub</a></p>
</article></body></html>'''

MARKDOWN='''# {{TITLE}}

摘要：{{SUMMARY}}

【封面：使用同文件夹中的“作品封面_1920x1080.png”】

如果家人独自在家，我们惦记的，往往是一个简单的问题：有没有发生需要留意的情况？

摄像头留下了画面，但真正值得关注的几秒，可能藏在很长的录像里。这次参加AI黑客松，我做了一个名叫「安居视界」的作品，希望让AI帮我们整理这些线索，也让每个判断都能回到视频中核对。

作品参加的是「智能日常」赛道：让AI真正进入每一天，而不只待在聊天框。

## 它能帮我们看什么？

### 关注家中人的活动
上传一段录像后，系统整理人物出现、行走、坐下等活动，并将疑似跌倒、求助等重要线索优先呈现。它不识别具体身份，而是围绕画面中的人物和行为组织记录。

### 留意家庭危险线索
烟雾、明火和火花等候选事件会单独展示。点击提醒，就能回看相关视频片段和关键帧，进一步理解画面里发生了什么。

### 像聊天一样，追问这段录像
你可以问：“这段录像有哪些需要关注的情况？”“什么时候出现疑似跌倒？”也可以追溯物品最后出现的位置。回答关联已有事件和视频证据，并区分直接观察与上下文推测。

## 已经做到哪一步？

目前已完成可在线体验的原型：视频上传、AI事件提取、事件时间线、证据回看和自然语言问答均已串联。线上可以直接查看火光/明火、白天模拟跌倒、低照度模拟跌倒及桌面长录像的真实分析案例，也能创建自己的副本重新分析。

当前分析的是上传录像，实时摄像头与手机推送还在后续计划中。跌倒案例为模拟行为；识别线索可能漏检或误判，需要结合原片理解，不替代专业探测设备或实时照护。

## 想请你，帮作品投一票

如果你也希望AI能在日常关怀里发挥作用，想请你为「安居视界」投一票。

【在这里插入“安居视界_投票二维码.png”，请上传图片，不要把本提示留在正式文章里】

长按识别二维码，进入我的作品页，按页面提示投票。

如果你也认同这个方向，欢迎转发给朋友。谢谢每一份支持！

投票链接：{{VOTE}}

在线体验：{{DEMO}}

项目源码：{{GITHUB}}
'''

INSTRUCTIONS='''安居视界｜微信投票宣传素材

1. 朋友圈或微信群
发送“安居视界_朋友圈投票海报.png”，配文可使用“朋友圈和微信群_配文.txt”。JPG为备用。二维码PNG可单独发送。

2. 微信公众号推文
“微信公众号推文_排版稿.html”是可以在浏览器打开的排版预览；“微信公众号推文_可复制文字.md”含标题、摘要及完整正文。使用你的公众号后台创建图文，把文稿复制进去，并将本包中的封面、二维码单独上传为文章图片。浏览器预览里的嵌入图片不保证能直接粘贴进微信编辑器，以上传图片为准。
发布前删除文字稿中的图片占位提示。如果后台支持“阅读原文”，可以将下面的投票链接填入该字段。

3. 正式微信推文链接
本包提供草稿与素材，没有登录或发布到你的公众号。只有在公众号后台发布后，才能获得正式的mp.weixin.qq.com文章链接。HTML本地文件不是可对外转发的公众号链接。

4. 二维码与投票规则
二维码使用你提供的完整作品链接：
{{VOTE}}
已对独立二维码、海报PNG/JPG及其50%缩小版本做本地解码校验；没有代投票，也没有假定每日票数、登录方式或投票截止时间。实际微信内打开、账号登录及投票流程仍需你用手机确认，并以赛事页面提示为准。

5. 素材说明
海报沿用原项目概念封面，新增排版与二维码；封面不表示已经接入实时摄像头。未调用付费模型生成本次素材。
'''


if __name__=='__main__':
    main()
