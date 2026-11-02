import type { ReactNode } from 'react';
import { stamp } from './ui';
import { confidenceLabel, isPriorityEvent, visibleText, type ConfidenceLevel } from './confidence';
import { eventDescription } from './eventPresentation';

export type TimelineEvent = { id: string; start_ms: number; end_ms: number; subject_id: string; target_id: string | null; action: string; epistemic_type: string; rule_id?: string | null; evidence_ids: string[]; uncertainty_reasons: string[]; review_status?: string; model_score?: number | null };
export type EventGroup = { id: string; kind: 'single' | 'repeated_observations' | 'overlapping_actions' | 'consecutive_actions'; event_ids: string[]; evidence_ids: string[]; start_ms: number; end_ms: number };
export type TimelineRow = { id: string; kind: EventGroup['kind']; members: TimelineEvent[] };

export function timelineRows(events: TimelineEvent[], groups: EventGroup[], folded: boolean): TimelineRow[] {
  if (!folded) return events.map(e => ({ id: e.id, kind: 'single', members: [e] }));
  const lookup = new Map(events.map(e => [e.id, e]));
  const used = new Set<string>();
  const rows: TimelineRow[] = [];
  // Filter members inside existing groups. Never regroup after filtering: excluded
  // movements/unknowns must still separate observations on either side.
  for (const group of groups) {
    let members: TimelineEvent[] = [];
    const flush = () => { if (members.length) rows.push({ id:group.id+':'+members[0].id,kind:members.length === 1 ? 'single' : group.kind,members }); members=[]; };
    for (const id of group.event_ids) {
      const e = lookup.get(id);
      if (!e) { flush(); continue; }
      used.add(e.id);members.push(e);
    }
    flush();
  }
  for (const e of events) if (!used.has(e.id)) rows.push({ id: e.id, kind: 'single', members: [e] });
  return rows.sort((a, b) => a.members[0].start_ms-b.members[0].start_ms || a.members[0].end_ms-b.members[0].end_ms);
}

type Props = {
  rows: TimelineRow[];
  isMock: boolean;
  label: (id: string | null) => string;
  actionName: Record<string, string>;
  references: (ids: string[]) => ReactNode;
  confidenceOf: (event: TimelineEvent) => ConfidenceLevel;
  entityType: (id: string) => string | undefined;
};

export function EventTimeline({ rows, isMock, label, actionName, references, confidenceOf, entityType }: Props) {
  const title = (e: TimelineEvent) => `${e.epistemic_type === 'inferred' ? '可能：' : ''}${eventDescription(e,label,entityType,actionName)}`;
  const status = (e: TimelineEvent) => isMock ? '测试模拟记录 · 未执行AI识别' : e.epistemic_type === 'inferred' ? '上下文推测 · 尚未证实' : e.model_score == null ? confidenceOf(e) === 'high' ? '多时刻画面证据 · 按证据分级' : '画面证据 · 按证据分级' : '模型观察 · 可点击时间回看证据';
  const body = (e: TimelineEvent) => <><p>{title(e)}</p><small>{status(e)}</small><div>{references(e.evidence_ids)}</div>{e.uncertainty_reasons.length > 0 && <small className="reason">{visibleText(e.uncertainty_reasons.join('；'))}</small>}</>;
  return rows.map(row => {
    const e = row.members[0]; const grouped = row.members.length > 1;
    const mixedFall = grouped && e.action === 'fall' && new Set(row.members.map(source => source.epistemic_type)).size > 1;
    const last = Math.max(...row.members.map(source => source.end_ms));
    const level = row.members.some(source => confidenceOf(source) === 'hidden') ? 'hidden'
      : row.members.some(source => confidenceOf(source) === 'medium') ? 'medium' : 'high';
    if (level === 'hidden') return null;
    return <article className={`event confidence-${level}${isPriorityEvent(e) ? ' priority-event' : ''}${grouped ? ' event-group' : ''}`} key={row.id} data-source-count={row.members.length} data-confidence={level}>
      <time>{stamp(e.start_ms)}{grouped && <span className="group-end">至 {stamp(last)}</span>}</time>
      <div>{!grouped ? body(e) : <>
        <p>{mixedFall ? `可能：${eventDescription(e,label,entityType,actionName)}` : row.kind === 'repeated_observations' ? `多次看见：${eventDescription(e,label,entityType,actionName)}` : title(e)}</p>
        <small>{row.kind === 'repeated_observations' ? `${row.members.length}条采样观察 · 合并展示` : row.kind === 'consecutive_actions' ? `${row.members.length}条相邻行为记录 · 合并展示` : `${row.members.length}条共享证据的重复记录 · 合并展示`}</small>
        <small>{mixedFall ? '同一次疑似跌倒的模型观察与上下文推测 · 合并展示，尚未确证' : status(e)}</small>
        <details className="event-group-sources"><summary>展开 {row.members.length} 条采样记录</summary><ol>{row.members.map(source => <li key={source.id} className="event-source" data-event-id={source.id}><time>{stamp(source.start_ms)}–{stamp(source.end_ms)}</time>{body(source)}</li>)}</ol></details>
      </>}</div>
      <span className={`tag ${level === 'medium' ? 'amber' : ''}`}>{isPriorityEvent(e) ? '重点关注 · ' : ''}{confidenceLabel(level)}</span>
    </article>;
  });
}
