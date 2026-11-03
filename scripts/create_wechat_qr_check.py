"""Create a local QR compatibility test; do not claim a WeChat navigation test."""
import json
from pathlib import Path
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'data/tool-deps/qr-compat'))
import qrcode


def main():
    materials = json.loads((ROOT / 'data/vote-materials.json').read_text(encoding='utf8'))
    original = materials['vote_url']
    parts = urlsplit(original)
    # Remove only the advertising attribution parameter; keep application and work routes.
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != 'spm']
    target = urlunsplit(parts._replace(query=urlencode(query)))
    assert parts.fragment == urlsplit(target).fragment == 'works/294001'
    assert target.isascii() and target.startswith('https://')
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    qr.add_data(target.encode('ascii'), optimize=0)
    qr.make(fit=True)
    matrix = np.where(np.array(qr.get_matrix(), dtype=bool), 0, 255).astype(np.uint8)
    pixels = cv2.resize(matrix, None, fx=12, fy=12, interpolation=cv2.INTER_NEAREST)
    decoded = cv2.QRCodeDetector().detectAndDecode(pixels)[0]
    assert decoded == target
    out = ROOT / 'deliverables/安居视界_微信扫码兼容测试'
    out.mkdir(parents=True, exist_ok=True)
    path = out / '投票二维码_微信测试版.png'
    ok, data = cv2.imencode('.png', pixels)
    assert ok
    data.tofile(path)
    (out / '验证记录.json').write_text(json.dumps({
        'original_url': original, 'encoded_url': target,
        'encoder': 'python-qrcode 8.2, ASCII byte mode, no ECI',
        'removed_query_parameters': ['spm'],
        'work_fragment_preserved': True,
        'opencv_decoded_exact_match': decoded == target,
        'wechat_phone_tested': False,
        'vote_page_navigation_verified': False,
        'original_poster_replaced': False,
    }, ensure_ascii=False, indent=2), encoding='utf8')
    (out / '使用说明.txt').write_text(
        '这是微信扫码兼容性测试版，尚未在手机微信中验证自动跳转。\n'
        '二维码只包含HTTPS网址，使用标准ASCII字节编码；仅移除spm追踪参数，保留活动参数和作品路由。\n'
        '请先保存图片，再通过手机微信扫一扫或相册识别。\n'
        '若仍只显示文字，请优先从赛事作品页的分享入口获取官方分享链接或二维码。\n'
        '原海报和推文未替换，待确认可以打开正确作品页后再更新。\n'
        + target + '\n', encoding='utf8')
    print(json.dumps({'path': str(path), 'width': pixels.shape[1],
                      'url': target, 'decoded_exact_match': True,
                      'wechat_tested': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
