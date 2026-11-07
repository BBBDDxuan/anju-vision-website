import type { ReactNode } from 'react';
import { stamp } from './ui';
import { type ConfidenceLevel } from './confidence';
import { eventTitle, eventSentence } from './familyPresentation';
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
  selectedEventId?: string | null;
  onSelect?: (event: TimelineEvent) => void;
};

export function EventTimeline({ rows, isMock, label, actionName, references, confidenceOf, entityType, selectedEventId, onSelect }: Props) {
  return rows.map(row => {
    const e = row.members[0];
    if (row.members.some(source => confidenceOf(source) === 'hidden')) return null;
    const selected = row.members.some(member => member.id === selectedEventId);
    const refs = [...new Set(row.members.flatMap(member => member.evidence_ids))];
    return <article className={`event family-event${selected ? ' is-selected' : ''}`} key={row.id} data-event-id={e.id} data-source-count={row.members.length}>
      <div className="family-event-heading"><strong>{eventTitle(e.action)}</strong><time><button className="event-select" aria-label={`选中事件 ${stamp(e.start_ms)}`} aria-pressed={selected} onClick={() => onSelect?.(e)}>{stamp(e.start_ms)}–{stamp(Math.max(...row.members.map(source => source.end_ms)))}</button></time></div>
      <p>{['fall','help_gesture','flame','smoke','sparks','long_absence'].includes(e.action) ? eventSentence(e.action) : `${e.epistemic_type === 'inferred' ? '可能：' : ''}${eventDescription(e, label, entityType, actionName)}。`}</p>
      {isMock && <small>界面测试数据，未执行实际识别。</small>}
      <div className="family-event-links"><button className="text-button" onClick={() => onSelect?.(e)}>查看视频</button>{references(refs)}</div>
    </article>;
  });
}
