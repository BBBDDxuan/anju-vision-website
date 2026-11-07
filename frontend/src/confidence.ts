/** Display tiers, not calibrated probabilities. Never impute a missing model score. */
import { ambiguousPersonRelation, eventDescription } from './eventPresentation.ts';
export type ConfidenceLevel = 'high' | 'medium' | 'hidden';
export const HIGH_SCORE = 0.75;
export const MIN_SCORE = 0.55;
export const isPriorityEvent = (event: { action: string }) => ['fall','help_gesture','long_absence','smoke','flame','sparks'].includes(event.action);

export type ConfidenceEvent = {
  id: string; subject_id: string; target_id: string | null; action: string; epistemic_type: string;
  start_ms: number; end_ms: number; evidence_ids: string[]; uncertainty_reasons: string[];
  model_score?: number | null; rule_id?: string | null;
};
type ConfidenceMemory = {
  events: ConfidenceEvent[];
  entities: { id: string; label: string; identity_status?: string; entity_type?: string }[];
  evidence: { id: string; frame_ids: string[] }[];
  frames: { id: string; pts_ms: number; kind?: string }[];
};
export function confidenceIndex(memory: ConfidenceMemory | null) {
  return {
    events: new Map(memory?.events.map(e => [e.id, e]) || []),
    entities: new Map(memory?.entities.map(e => [e.id, e]) || []),
    evidence: new Map(memory?.evidence.map(e => [e.id, e]) || []),
    frames: new Map(memory?.frames.map(f => [f.id, f]) || []),
  };
}
export type ConfidenceIndex = ReturnType<typeof confidenceIndex>;

export function eventConfidence(event: ConfidenceEvent, index: ConfidenceIndex): ConfidenceLevel {
  if (event.epistemic_type === 'unknown' || event.action === 'unknown') return 'hidden';
  const score = event.model_score;
  if (score != null && (!Number.isFinite(score) || score < MIN_SCORE || score > 1)) return 'hidden';
  if (!index.entities.has(event.subject_id) || (event.target_id && !index.entities.has(event.target_id))) return 'hidden';
  if (['place_on','put_in','take_out'].includes(event.action) && !event.target_id) return 'hidden';
  if (!event.evidence_ids.length) return 'hidden';
  const times = new Set<number>();
  for (const ref of event.evidence_ids) {
    const evidence = index.evidence.get(ref);
    if (!evidence?.frame_ids.length) return 'hidden';
    for (const fid of evidence.frame_ids) {
      const frame = index.frames.get(fid);
      if (!frame || (frame.kind && frame.kind !== 'original_frame') || frame.pts_ms < event.start_ms || frame.pts_ms > event.end_ms) return 'hidden';
      times.add(frame.pts_ms);
    }
  }
  if (['put_in','take_out','explicit_exit'].includes(event.action) && times.size < 2) return 'hidden';
  if (event.epistemic_type === 'inferred' && (times.size < 2 || !event.uncertainty_reasons.length)) return 'hidden';
  const ambiguous = [event.subject_id,event.target_id].some(id => id && index.entities.get(id)?.identity_status === 'ambiguous');
  if (event.epistemic_type !== 'observed' || ambiguous || event.uncertainty_reasons.length
      || ambiguousPersonRelation(event,id => index.entities.get(id)?.entity_type)
      || (event.action !== 'seen_at' && times.size < 2)) return 'medium';
  if (score == null) {
    // Evidence-based display tier only: leave the missing model score untouched.
    // Distinct references to the same instant do not corroborate an observation.
    const ordered = [...times].sort((a,b) => a-b);
    return ordered.length >= 2 && ordered[ordered.length-1]-ordered[0] >= 1000 ? 'high' : 'medium';
  }
  if (score < HIGH_SCORE) return 'medium';
  return 'high';
}

export function visibleText(text: string): string {
  return text.replace(/[^。；]*?(?:人工复核|人工确认|人工纠正|待复核|AI复核|历史确认|历史核验)[^。；]*/g, '相关情况暂时无法确认');
}

export type SourceClaim = { text: string; event_ids?: string[]; evidence_ids: string[]; rule_id?: string; review_status?: string; record_summary?: boolean };
export type DisplayClaim = SourceClaim & { confidence: 'high' | 'medium'; time_range?: string; explanations?: string[] };
const phrase: Record<string,string> = { seen_at:'出现在',place_on:'被放在',pick_up:'被拿起',put_in:'被放入',take_out:'被取出于',move:'移动到',open:'被打开',close:'被关闭',explicit_exit:'离开视野',occluded:'被遮挡',walk:'行走',sit_down:'坐下',stand_up:'起身',lie_down:'躺下',lying_on_floor:'躺卧于地面',fall:'出现疑似跌倒过程',climb:'攀爬',help_gesture:'做出挥手动作',no_person_visible:'所引用采样画面未见人物' };
const timeLabel = (ms: number) => `${String(Math.floor(ms/60000)).padStart(2,'0')}:${(ms/1000%60).toFixed(1).padStart(4,'0')}`;

export function displayClaim(claim: SourceClaim, index: ConfidenceIndex, inferred = false): DisplayClaim | null {
  if (!claim.event_ids?.length) return null;
  const members = claim.event_ids.map(id => index.events.get(id));
  if (members.some(e => !e)) return null;
  const sources = members as ConfidenceEvent[];
  const levels = sources.map(e => eventConfidence(e,index));
  const conditional = claim.rule_id === 'conditional_containment_v1';
  if (conditional && levels.includes('hidden')) return null;
  const kept = sources.filter((_,i) => levels[i] !== 'hidden');
  if (!kept.length) return null;
  const confidence = inferred || conditional || levels.includes('medium') ? 'medium' : 'high';
  // Validated record summaries can span different actions. Never display a
  // summary if any of its supporting events became hidden in this revision.
  if (claim.record_summary) return kept.length === sources.length ? {...claim, text:visibleText(claim.text), confidence} : null;
  if (inferred && !conditional) {
    const first = kept[0];
    if (!kept.every(e => e.subject_id === first.subject_id && e.target_id === first.target_id && e.action === first.action)) return null;
    const label = (id: string | null) => {
      const entity = id ? index.entities.get(id) : undefined;
      return entity?.entity_type === 'person' ? entity.label.match(/^人物\d+/)?.[0] || entity.label : entity?.label || '';
    };
    const type = (id: string) => index.entities.get(id)?.entity_type;
    const description = ambiguousPersonRelation(first,type)
      ? `${label(first.subject_id)} 与${label(first.target_id)}相关的活动（动作未明确）`
      : eventDescription(first,label,type,phrase);
    const subject = index.entities.get(first.subject_id);
    const explanations = [...new Set([
      ...(subject && subject.label !== label(subject.id) ? [`人物描述：${subject.label}`] : []),
      ...kept.flatMap(e => e.uncertainty_reasons),
    ])].map(visibleText);
    return {...claim, text:`可能：${description}`, confidence:'medium',
      time_range:`${timeLabel(Math.min(...kept.map(e => e.start_ms)))}–${timeLabel(Math.max(...kept.map(e => e.end_ms)))}`,
      explanations, event_ids:kept.map(e => e.id), evidence_ids:[...new Set(kept.flatMap(e => e.evidence_ids))]};
  }
  if (kept.length === sources.length) return { ...claim, text:visibleText(claim.text), confidence };
  // A grouped history can contain both visible and hidden sources. Rebuild only
  // from surviving observations instead of retaining the old time span or refs.
  const first = kept[0];
  if (!kept.every(e => e.subject_id === first.subject_id && e.target_id === first.target_id && e.action === first.action)) return null;
  const start = timeLabel(Math.min(...kept.map(e => e.start_ms)));
  const end = timeLabel(Math.max(...kept.map(e => e.end_ms)));
  const description = eventDescription(first, id => id ? index.entities.get(id)?.label || '' : '',
    id => index.entities.get(id)?.entity_type, phrase);
  let text = `在${start}–${end}，${inferred ? '可能：' : ''}${description}。`;
  if (kept.length > 1) text += ` 共${kept.length}条采样记录，不表示期间始终在场。`;
  return { ...claim, text, confidence, event_ids:kept.map(e => e.id), evidence_ids:[...new Set(kept.flatMap(e => e.evidence_ids))] };
}
