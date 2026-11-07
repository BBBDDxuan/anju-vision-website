// One cancellable seek path for events, question citations and the timebar.
export async function seekVideo(video: HTMLVideoElement, timeMs: number, signal: AbortSignal): Promise<number> {
  const wait = (event: 'loadedmetadata' | 'seeked') => new Promise<void>((resolve, reject) => {
    const clean = () => { clearTimeout(timer); video.removeEventListener(event, ready); video.removeEventListener('error', fail); signal.removeEventListener('abort', cancel); };
    const ready = () => { clean(); resolve(); };
    const fail = () => { clean(); reject(new Error('画面定位失败，请检查媒体服务后重试。')); };
    const cancel = () => { clean(); reject(new DOMException('定位已取消', 'AbortError')); };
    const timer = setTimeout(fail, 12000);
    video.addEventListener(event, ready, { once: true }); video.addEventListener('error', fail, { once: true }); signal.addEventListener('abort', cancel, { once: true });
    if (signal.aborted) cancel();
  });
  video.pause();
  if (video.error) throw new Error('视频暂不可用，请检查媒体服务。');
  if (video.readyState < 1) await wait('loadedmetadata');
  if (signal.aborted) throw new DOMException('定位已取消', 'AbortError');
  if (!Number.isFinite(video.duration) || !Number.isFinite(timeMs)) throw new Error('视频时间信息暂不可用。');
  const target = Math.min(Math.max(timeMs / 1000, 0), video.duration);
  if (Math.abs(video.currentTime - target) > .005 || video.seeking) {
    const settled = wait('seeked'); video.currentTime = target; await settled;
  }
  return video.currentTime;
}
