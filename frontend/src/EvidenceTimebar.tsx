import './evidence-inspector.css';
import type { CSSProperties } from 'react';

export type TimeRange = { id: string; startMs: number; endMs: number; label: string; selected?: boolean };
export function EvidenceTimebar({ durationSec, timeSec, ranges, coverage = [], onSeek, onSelect, label = '视频时间定位' }: {
  durationSec: number; timeSec: number; ranges: TimeRange[]; coverage?: { start_ms: number; end_ms: number }[];
  onSeek: (seconds: number) => void; onSelect?: (id: string) => void; label?: string;
}) {
  const total = Math.max(.01, durationSec);
  const position = (ms: number) => Math.max(0, Math.min(100, ms / (total * 1000) * 100));
  const clock = (sec: number) => `${String(Math.floor(sec / 60)).padStart(2, '0')}:${(sec % 60).toFixed(2).padStart(5, '0')}`;
  return <div className="evidence-timebar">
    <div className="evidence-timebar-track" style={{ '--cursor': `${position(timeSec * 1000)}%` } as CSSProperties}>
      {coverage.map((range, i) => <span className="timebar-coverage" key={i} style={{ left: `${position(range.start_ms)}%`, width: `${position(range.end_ms) - position(range.start_ms)}%` }}/>) }
      {ranges.map(range => <span key={range.id} className={`timebar-evidence${range.selected ? ' is-selected' : ''}`} title={range.label} style={{ left: `${position(range.startMs)}%`, width: `${Math.max(.4, position(range.endMs) - position(range.startMs))}%` }}/>) }
      <span className="timebar-cursor"/>
      <input aria-label={label} aria-valuetext={clock(timeSec)} type="range" min={0} max={total} step=".01" value={Math.min(total, timeSec)} onChange={e => onSeek(Number(e.target.value))}/>
    </div>
    <div className="timebar-labels"><time>{clock(timeSec)}</time><span>事件证据{coverage.length > 0 && ' · 已分析'}</span><time>{clock(durationSec)}</time></div>
    {onSelect && <div className="timebar-markers" aria-label="精简事件时间线">{ranges.length ? ranges.map(range => <button key={range.id} aria-pressed={range.selected || false} onClick={() => onSelect(range.id)} title={range.label}>{clock(range.startMs / 1000)} · {range.label}</button>) : <span>当前没有可展示的事件标记</span>}</div>}
  </div>;
}
