import type { ReactNode } from 'react';

export function Icon({ name, size = 20, className }: { name: string; size?: number; className?: string }) {
  const paths: Record<string, ReactNode> = {
    monitor: <><rect x="3" y="3" width="18" height="13" rx="2"/><path d="M12 16v5m-5 0h10"/></>,
    play: <path d="m9 5 11 7-11 7Z"/>,
    pause: <><path d="M8 5v14M16 5v14"/></>,
    user: <><circle cx="12" cy="7" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/></>,
    warning: <><path d="m10.3 4.4-8 14A1.8 1.8 0 0 0 4 21h16a1.8 1.8 0 0 0 1.6-2.6l-8-14a1.8 1.8 0 0 0-3.3 0Z"/><path d="M12 9v4m0 4h.01"/></>,
    activity: <><circle cx="15" cy="4" r="2"/><path d="m4 10 5-3 5 4 5 1 2-3M4 20l5-5 5 1 2 5M9 15l3-5M5 6l-2 1"/></>,
    'help-gesture': <><circle cx="12" cy="8" r="2.5"/><path d="m3 3 4 8 5 2 5-2 4-8M12 13v8m-5 0h10"/></>,
    flame: <path d="M12 2c2 6-4 7-4 12-2-1-3-3-3-3-5 9 4 14 10 10 6-4 4-10 1-14 0 5-3 5-3 5 2-6-1-10-1-10Z"/>,
    layers: <><path d="m12 3 10 5-10 5L2 8Zm-9 9 9 5 9-5m-18 5 9 5 9-5"/></>,
    chip: <><rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4M9 14v-4l3 4v-4m3 0v4"/></>,
    document: <><path d="M14 2H5v20h14V7Zm0 0v5h5M8 11h8m-8 4h8m-8 4h5"/></>,
    plug: <><path d="M8 2v6m8-6v6M5 8h14v5a7 7 0 0 1-14 0Zm7 12v2"/></>,
    home: <><path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z" /></>,
    video: <><rect x="3" y="5" width="13" height="14" rx="3" /><path d="m16 10 5-3v10l-5-3" /></>,
    upload: <><path d="M12 16V3m-5 5 5-5 5 5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4" /></>,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    chat: <><path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2v-10A9 9 0 1 1 21 11.5Z" /><path d="M7 10h10M7 14h6" /></>,
    spark: <><path d="m12 2 2.6 6.9L22 12l-7.4 3.1L12 22l-2.6-6.9L2 12l7.4-3.1Z" /></>,
    shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z" /><path d="m8 12 3 3 5-6" /></>,
    search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></>,
    arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
    'arrow-up-right': <path d="M6 18 18 6M6 6h12v12" />,
    'chevron-up': <path d="m6 15 6-6 6 6" />,
    'chevron-down': <path d="m6 9 6 6 6-6" />,
    replay: <path d="M3 11a9 9 0 1 1 2.7 6.4M3 4v7h7" />,
    menu: <path d="M4 6h16M4 12h16M4 18h16" />,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    trash: <><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" /></>,
    help: <><circle cx="12" cy="12" r="9" /><path d="M9.5 8.5a2.5 2.5 0 1 1 4 2c-1 .6-1.5 1-1.5 2.5M12 17h.01" /></>,
  };
  return <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{paths[name] || paths.video}</svg>;
}

export function stamp(ms: number) {
  const s = Math.max(0, ms) / 1000;
  const minutes = `${Math.floor(s / 60 % 60)}`.padStart(2, '0');
  const seconds = (s % 60).toFixed(1).padStart(4, '0');
  return s >= 3600 ? `${Math.floor(s / 3600).toString().padStart(2, '0')}:${minutes}:${seconds}` : `${minutes}:${seconds}`;
}

export const sizeLabel = (bytes: number) => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1).replace(/\.0$/, '')} GiB` : `${Math.round(bytes / 1024 ** 2)} MB`;
export const durationLabel = (ms: number) => ms >= 3600000 ? `${Math.floor(ms / 3600000)}小时${Math.floor(ms / 60000 % 60) ? `${Math.floor(ms / 60000 % 60)}分` : ''}` : ms >= 60000 ? `${Math.floor(ms / 60000)}分${Math.floor(ms / 1000 % 60)}秒` : `${Math.floor(ms / 1000)}秒`;

export function mergedRanges(ranges: { start_ms: number; end_ms: number }[]) {
  const merged: { start_ms: number; end_ms: number }[] = [];
  for (const r of [...ranges].sort((a, b) => a.start_ms - b.start_ms)) {
    const last = merged.at(-1);
    if (last && r.start_ms <= last.end_ms) last.end_ms = Math.max(last.end_ms, r.end_ms);
    else merged.push({ ...r });
  }
  return merged;
}
