import { useEffect, type RefObject } from 'react';

/** A single video-driven clock for every overlay consumer. No React render per
 * frame; no independent elapsed-time animation. Pauses/hidden tabs cancel work. */
export function useVideoFrame(videoRef: RefObject<HTMLVideoElement | null>, onFrame: (seconds: number) => void) {
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    let handle: number | undefined, mode: 'video' | 'animation' = 'video', visible = true;
    const cancel = () => {
      if (handle === undefined) return;
      if (mode === 'video') video.cancelVideoFrameCallback(handle);
      else cancelAnimationFrame(handle);
      handle = undefined;
    };
    const sync = () => onFrame(video.currentTime);
    const schedule = () => {
      if (handle !== undefined || video.paused || video.ended || document.hidden || !visible) return;
      if (typeof video.requestVideoFrameCallback === 'function') {
        mode = 'video';
        handle = video.requestVideoFrameCallback((_now, metadata) => {
          handle = undefined; onFrame(metadata.mediaTime); schedule();
        });
      } else {
        mode = 'animation';
        handle = requestAnimationFrame(() => { handle = undefined; sync(); schedule(); });
      }
    };
    const refresh = () => { cancel(); sync(); schedule(); };
    const stop = () => { cancel(); sync(); };
    const hidden = () => { if (document.hidden) cancel(); else refresh(); };
    const events = ['loadedmetadata', 'loadeddata', 'seeking', 'seeked', 'timeupdate', 'emptied', 'ratechange'];
    events.forEach(event => video.addEventListener(event, refresh));
    video.addEventListener('play', refresh);
    video.addEventListener('pause', stop);
    video.addEventListener('ended', stop);
    document.addEventListener('visibilitychange', hidden);
    const observer = new IntersectionObserver(entries => {
      visible = entries[0].isIntersecting;
      if (visible) refresh(); else cancel();
    });
    observer.observe(video);
    refresh();
    return () => {
      cancel(); observer.disconnect();
      events.forEach(event => video.removeEventListener(event, refresh));
      video.removeEventListener('play', refresh);
      video.removeEventListener('pause', stop);
      video.removeEventListener('ended', stop);
      document.removeEventListener('visibilitychange', hidden);
    };
  }, [videoRef, onFrame]);
}
