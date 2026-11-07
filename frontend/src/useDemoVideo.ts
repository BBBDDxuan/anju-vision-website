import { useCallback, useEffect, useRef, useState } from 'react';

type VideoCommand = { start?: number; end?: number; play?: boolean };

function waitFor(video: HTMLVideoElement, event: 'loadedmetadata' | 'seeked', signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const cleanup = () => {
      clearTimeout(timeout); video.removeEventListener(event, ready);
      video.removeEventListener('error', fail); signal.removeEventListener('abort', abort);
    };
    const ready = () => { cleanup(); resolve(); };
    const fail = () => { cleanup(); reject(new Error('视频加载失败，请检查文件或浏览器编码支持。')); };
    const abort = () => { cleanup(); reject(new DOMException('Cancelled', 'AbortError')); };
    const timeout = setTimeout(fail, 12000);
    video.addEventListener(event, ready, { once: true });
    video.addEventListener('error', fail, { once: true });
    signal.addEventListener('abort', abort, { once: true });
    if (signal.aborted) abort();
  });
}

// Every demo uses this hook, including the workflow's single persistent video.
// New playback also cancels an older pending seek, so slow media cannot resume
// after the user selects another card, changes stages or leaves the viewport.
export function useDemoVideo() {
  const ref = useRef<HTMLVideoElement>(null);
  const operation = useRef<AbortController | null>(null);
  const playRequest = useRef(false);
  const boundary = useRef<number | undefined>(undefined);
  const loopBeforeSegment = useRef<boolean | null>(null);
  const [playing, setPlaying] = useState(false);
  const [busy, setBusy] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [buffering, setBuffering] = useState(false);
  const [error, setError] = useState('');

  const restoreLoop = useCallback(() => {
    if (ref.current && loopBeforeSegment.current !== null) ref.current.loop = loopBeforeSegment.current;
    loopBeforeSegment.current = null;
  }, []);
  const finishSegment = useCallback(() => {
    const video = ref.current;
    const end = boundary.current;
    if (!video || end === undefined) return;
    boundary.current = undefined; playRequest.current = false;
    video.pause();
    video.currentTime = Math.min(end, video.duration);
    restoreLoop(); setCurrentTime(video.currentTime); setPlaying(false);
  }, [restoreLoop]);
  const pause = useCallback(() => {
    playRequest.current = false; operation.current?.abort();
    boundary.current = undefined;
    ref.current?.pause(); restoreLoop(); setBusy(false); setPlaying(false); setBuffering(false);
  }, [restoreLoop]);
  useEffect(() => {
    if (!playing || boundary.current === undefined) return;
    let frame = 0;
    const check = () => {
      const video = ref.current;
      if (!video || video.paused || boundary.current === undefined) return;
      if (video.currentTime >= boundary.current) finishSegment();
      else frame = requestAnimationFrame(check);
    };
    frame = requestAnimationFrame(check);
    return () => cancelAnimationFrame(frame);
  }, [playing, finishSegment]);
  useEffect(() => {
    const video = ref.current;
    if (!video) return;
    const stopOthers = (event: Event) => { if (event.target !== video) pause(); };
    const visibility = () => { if (document.hidden) pause(); };
    document.addEventListener('av-demo-play', stopOthers);
    document.addEventListener('av-demo-pause', pause);
    document.addEventListener('visibilitychange', visibility);
    const observer = new IntersectionObserver(entries => {
      if (!entries[0].isIntersecting || entries[0].intersectionRatio < .12) pause();
    }, { threshold: [0, .12] });
    observer.observe(video);
    return () => {
      operation.current?.abort(); video.pause(); restoreLoop(); observer.disconnect();
      document.removeEventListener('av-demo-play', stopOthers);
      document.removeEventListener('av-demo-pause', pause);
      document.removeEventListener('visibilitychange', visibility);
    };
  }, [pause, restoreLoop]);

  const command = useCallback(async ({ start, end, play = false }: VideoCommand) => {
    const video = ref.current;
    if (!video) return false;
    operation.current?.abort();
    const controller = new AbortController(); operation.current = controller;
    const { signal } = controller;
    restoreLoop();
    playRequest.current = play; boundary.current = end;
    // A near-end segment must finish before native looping jumps to zero.
    // Preserve the caller's loop setting for full-video playback afterwards.
    if (end !== undefined) { loopBeforeSegment.current = video.loop; video.loop = false; }
    video.pause(); setError(''); setBusy(true);
    if (play) video.dispatchEvent(new Event('av-demo-play', { bubbles: true }));
    try {
      if (video.error) throw new Error('视频加载失败，请检查文件或浏览器编码支持。');
      if (video.readyState < 1) await waitFor(video, 'loadedmetadata', signal);
      if (signal.aborted) return false;
      if (start !== undefined) {
        const target = Math.min(Math.max(0, start), video.duration);
        if (Math.abs(video.currentTime - target) > .005 || video.seeking) {
          const seeked = waitFor(video, 'seeked', signal);
          video.currentTime = target;
          await seeked;
        }
        setCurrentTime(video.currentTime);
      }
      if (signal.aborted) return false;
      video.muted = true;
      if (play) await video.play();
      return !signal.aborted;
    } catch (cause) {
      if (!signal.aborted) setError(cause instanceof Error ? cause.message : '视频暂时无法播放，请重试。');
      return false;
    } finally { if (!signal.aborted) setBusy(false); }
  }, [restoreLoop]);

  return { ref, playing, busy, currentTime, duration, buffering, error, pause, command, videoEvents: {
    onLoadedMetadata: () => setDuration(ref.current?.duration || 0),
    onDurationChange: () => { if (Number.isFinite(ref.current?.duration)) setDuration(ref.current!.duration); },
    onWaiting: () => setBuffering(true),
    onCanPlay: () => setBuffering(false),
    onPlay: () => {
      const video = ref.current!;
      video.muted = true;
      video.dispatchEvent(new Event('av-demo-play', { bubbles: true }));
      if (!playRequest.current) boundary.current = undefined;
      setPlaying(true);
    },
    onPause: () => setPlaying(false),
    onEnded: () => { finishSegment(); playRequest.current = false; setPlaying(false); },
    onTimeUpdate: () => {
      const video = ref.current!;
      if (boundary.current !== undefined && video.currentTime >= boundary.current) {
        finishSegment();
      }
      setCurrentTime(video.currentTime);
    },
    onSeeked: () => setCurrentTime(ref.current!.currentTime),
    onError: () => { pause(); setError('视频加载失败或浏览器不支持此编码，请检查媒体文件后刷新。'); },
  } };
}
