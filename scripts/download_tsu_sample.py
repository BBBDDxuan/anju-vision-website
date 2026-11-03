"""Fetch one authorized TSU video from the start of the official tar.gz stream.

No archive is saved and no model service is called. Passwords are read from
stdin/getpass, kept only in memory, and sent only to the fixed HTTPS origin.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import getpass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import threading
import time

import cv2
import httpx

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://repo-sam.inria.fr/smarthome/RGB.tar.gz'
DESTINATION = ROOT/'video/restricted_samples/Toyota_Smarthome_Untrimmed'


class HttpStream(io.RawIOBase):
    def __init__(self, response):
        self.chunks = response.iter_raw(chunk_size=128*1024)
        self.buffer = bytearray()
        self.received = 0

    def readable(self):
        return True

    def read(self, size=-1):
        if size < 0:
            raise ValueError('Unbounded archive reads are forbidden')
        while len(self.buffer) < size:
            chunk = next(self.chunks, b'')
            if not chunk:
                break
            self.received += len(chunk)
            self.buffer.extend(chunk)
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result


class RangeStream(io.RawIOBase):
    """A bounded four-request lookahead, consumed in original gzip order."""
    def __init__(self, client, auth, total, last_modified):
        self.client, self.auth, self.total = client, auth, total
        self.last_modified = last_modified
        self.block_size = 1024**2
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.buffer = bytearray()
        self.received = 0
        self.index = self.next_index = 0
        self.pending = {}
        for _ in range(4):
            self.prefetch()

    def prefetch(self):
        if self.next_index*self.block_size < self.total:
            self.pending[self.next_index] = self.pool.submit(self.fetch, self.next_index)
            self.next_index += 1

    def fetch(self, index):
        start = index*self.block_size
        end = min(self.total-1, start+self.block_size-1)
        headers = {'Range':f'bytes={start}-{end}', 'Accept-Encoding':'identity'}
        if self.last_modified:
            headers['If-Range'] = self.last_modified
        with self.client.stream('GET', URL, auth=self.auth, headers=headers) as response:
            if response.status_code != 206 or response.headers.get('content-range') != f'bytes {start}-{end}/{self.total}':
                raise RuntimeError('Range response changed or was ignored; refusing an unbounded download')
            result = bytearray()
            for chunk in response.iter_raw(chunk_size=64*1024):
                if self.stopping.is_set():
                    raise RuntimeError('Unused lookahead cancelled after the selected video')
                if len(result)+len(chunk) > end-start+1:
                    raise RuntimeError('Oversized range response')
                result.extend(chunk)
                with self.lock:
                    self.received += len(chunk)
            if len(result) != end-start+1:
                raise RuntimeError('Incomplete range response')
            return bytes(result)

    def read(self, size=-1):
        if size < 0:
            raise ValueError('Unbounded archive reads are forbidden')
        while len(self.buffer) < size and self.index in self.pending:
            block = self.pending.pop(self.index).result()
            self.index += 1
            self.prefetch()
            self.buffer.extend(block)
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result

    def close(self):
        if not self.closed:
            self.stopping.set()
            self.pool.shutdown(wait=True, cancel_futures=True)
        super().close()


def verify_video(path):
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise RuntimeError('Downloaded MP4 cannot be opened')
        fps = capture.get(cv2.CAP_PROP_FPS)
        frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if fps <= 0 or frames < 3:
            raise RuntimeError('Video metadata is invalid')
        decoded = []
        for index in [0, frames//2, max(0, frames-10)]:
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if not ok or frame is None:
                raise RuntimeError('Video decode failed at a sampled position')
            decoded.append(index)
        return {'duration_seconds': frames/fps, 'fps': fps, 'frame_count': frames,
                'width': width, 'height': height, 'decoded_sample_frames': decoded}
    finally:
        capture.release()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--password-stdin', action='store_true')
    parser.add_argument('--probe-ranges', action='store_true')
    parser.add_argument('--parallel-ranges', action='store_true')
    parser.add_argument('--resume-partial', action='store_true')
    args = parser.parse_args()
    if args.password_stdin:
        print('Awaiting temporary dataset password on stdin (not logged).', flush=True)
        password = sys.stdin.readline().rstrip('\r\n')
    else:
        password = getpass.getpass('Temporary dataset password: ')
    if not password:
        raise RuntimeError('No password supplied; nothing was downloaded')
    DESTINATION.mkdir(parents=True, exist_ok=True)
    # This directory is intentionally separate from distributable public samples.
    (DESTINATION/'.gitignore').write_text('*\n', encoding='utf8')
    timeout = httpx.Timeout(connect=30, read=90, write=30, pool=30)
    if args.probe_ranges:
        with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as client:
            with client.stream('GET', URL, auth=httpx.BasicAuth('Smarthome', password),
                               headers={'Range':'bytes=0-0','Accept-Encoding':'identity'}) as response:
                print('Range probe:', response.status_code,
                      {key:response.headers[key] for key in ['content-range','content-length','accept-ranges','etag','last-modified'] if key in response.headers}, flush=True)
        return
    with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as client:
        with client.stream('GET', URL, auth=httpx.BasicAuth('Smarthome', password),
                           headers={'Accept-Encoding':'identity'}) as response:
            if response.status_code != 200:
                raise RuntimeError(f'Official archive returned HTTP {response.status_code}; no redirect or credential forwarding attempted')
            print('Authenticated official archive; streaming one video only.', flush=True)
            if args.parallel_ranges:
                total = int(response.headers.get('content-length', '0'))
                if total <= 0 or response.headers.get('accept-ranges') != 'bytes':
                    raise RuntimeError('Server did not confirm byte-range support')
                modified = response.headers.get('last-modified')
                response.close()
                source = RangeStream(client, httpx.BasicAuth('Smarthome', password), total, modified)
                print('Using four bounded HTTP ranges; only the selected MP4 is saved.', flush=True)
            else:
                source = HttpStream(response)
            with source, tarfile.open(fileobj=source, mode='r|gz', bufsize=64*1024) as archive:
                for member in archive:
                    if source.received > 16*1024**2:
                        raise RuntimeError('Archive prefix exceeds 16 MiB without a video; stopped instead of scanning the full dataset')
                    if not member.isfile() or not member.name.lower().endswith('.mp4'):
                        if member.isfile() and member.size > 8*1024**2:
                            raise RuntimeError('Large non-video member precedes the sample; stopped to avoid unnecessary transfer')
                        continue
                    name = PurePosixPath(member.name).name
                    if not name or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-' for c in name):
                        raise RuntimeError('Unexpected video filename')
                    if member.size > 4*1024**3 or shutil.disk_usage(DESTINATION).free < member.size+1024**3:
                        raise RuntimeError('Sample exceeds the bounded transfer or available disk capacity')
                    target = DESTINATION/name
                    partial = target.with_suffix(target.suffix+'.part')
                    if target.exists() or (partial.exists() and not args.resume_partial):
                        raise FileExistsError('Existing sample/partial preserved; choose recovery explicitly')
                    print(json.dumps({'member':member.name,'bytes':member.size,'output':str(target)},ensure_ascii=False),flush=True)
                    digest = hashlib.sha256()
                    copied = 0
                    started = last_update = time.monotonic()
                    item = archive.extractfile(member)
                    if item is None:
                        raise RuntimeError('Selected archive member is not a regular file')
                    resume = partial.stat().st_size if partial.exists() else 0
                    if resume > member.size:
                        raise RuntimeError('Existing partial is larger than the archive member')
                    if resume:
                        with partial.open('rb') as previous:
                            while copied < resume:
                                old = previous.read(min(1024**2, resume-copied))
                                fresh = item.read(len(old))
                                if fresh != old:
                                    raise RuntimeError('Partial prefix differs from the official member; existing file preserved')
                                digest.update(fresh)
                                copied += len(fresh)
                        print('Verified and retained partial prefix:',resume,'bytes',flush=True)
                    with item, partial.open('ab' if resume else 'xb') as output:
                        while copied < member.size:
                            block = item.read(min(1024**2, member.size-copied))
                            if not block:
                                raise RuntimeError('Incomplete video; partial file retained')
                            output.write(block)
                            digest.update(block)
                            copied += len(block)
                            if time.monotonic()-last_update >= 15:
                                print(json.dumps({'downloaded_mib':round(copied/1024**2,1),
                                    'percent':round(copied/member.size*100,1),
                                    'elapsed_seconds':round(time.monotonic()-started)},ensure_ascii=False),flush=True)
                                last_update = time.monotonic()
                        output.flush()
                        os.fsync(output.fileno())
                    partial.rename(target)
                    network_bytes = source.received
                    archive_member = member.name
                    break
                else:
                    raise RuntimeError('No MP4 found in the bounded archive prefix')
            network_bytes = source.received
        # The HTTP stream is closed here, before reading another archive member.
    password = None
    metadata = verify_video(target)
    result = {'dataset':'Toyota Smarthome Untrimmed', 'source_url':URL,
              'archive_member':archive_member,'path':str(target),'bytes':target.stat().st_size,
              'sha256':digest.hexdigest(),'network_bytes_received':network_bytes,
              'whole_archive_downloaded':False,'model_calls':0,
              'transfer_mode':'parallel_http_ranges' if args.parallel_ranges else 'sequential_http_stream',
              'authoritative_individual_checksum_provided':False, **metadata}
    (DESTINATION/'sample.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    (DESTINATION/'SOURCE.md').write_text('''# Toyota Smarthome Untrimmed 本地研究样本

仅从官方 RGB.tar.gz 流式提取一个完整视频，未保存完整数据包。
访问凭据来自数据集官方临时授权，不在脚本、源码或说明中保存。
仅供获授权的本地非商业研究；许可限制第三方分发及商业使用。
不要加入公开作品包、Git仓库、魔搭创空间或发送给第三方模型API，除非取得许可方相应书面授权。

附件来源：D:/浏览器下载/License (TSU).pdf 与 README (TSU).txt。
说明文件标注提供视频为1.25倍速，标注以帧为单位，按25fps对齐；不要混淆播放时间与原始拍摄时间。
视频实际尺寸、帧率、播放时长与本地SHA256见 sample.json。
只抽查了起始、中间、末尾附近帧的解码；未验证全包gzip尾部校验，也没有声称验证全部视频帧。
没有调用任何模型或将画面上传到云端。
''', encoding='utf8')
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
