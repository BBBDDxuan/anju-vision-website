import fixture from '../public/demo/demo-public-fixture.json';

export type DemoCaseId = 'fall' | 'help' | 'fire';
export type DemoFrame = { id: string; ptsMs: number; image: string };
export type DemoEvidence = {
  title: string; type: string; startMs: number; endMs: number;
  localCandidateTriggerSec: number; reviewVerdict: string; reviewReason: string;
  humanConfirmed: boolean; frames: DemoFrame[];
};
export type DemoResultState =
  | { status: 'available'; source: 'verified_model_export'; result: DemoEvidence }
  | { status: 'pending_model_fix'; source: null; result: null };
export type DemoCase = {
  id: DemoCaseId; title: string; videoSrc: string; posterSrc: string; durationMs: number;
  previewStartSec: number; previewEndSec: number; disclaimer: string;
  modelResult: DemoResultState;
};
export type PublicDemoEvent = NonNullable<(typeof fixture.cases)[number]['event']>;

// Only the public, derived fixture enters the browser bundle. Raw model exports
// remain outside frontend/public, with hashes checked by prepare-web-demo.py.
export function demoAsset(path: string, base = (import.meta as ImportMeta & { env?: { BASE_URL?: string } }).env?.BASE_URL || '/') {
  if (!/^\/demo\/(videos|posters|keyframes)\/[\w.-]+$/.test(path)) throw new Error('Invalid public demo asset');
  return `${base.replace(/\/$/, '')}${path}`;
}

export const relativeTime = (ms: number) => {
  const centiseconds = Math.round(Math.max(0, ms) / 10);
  return `${String(Math.floor(centiseconds / 6000)).padStart(2, '0')}:${String(Math.floor(centiseconds / 100) % 60).padStart(2, '0')}.${String(centiseconds % 100).padStart(2, '0')}`;
};
export const evidenceRange = (event: DemoEvidence) => `${relativeTime(event.startMs)}–${relativeTime(event.endMs)}`;
export const reviewLabel = (event: DemoEvidence) => event.reviewVerdict === 'confirmed'
  ? `视觉模型复核支持${event.type === 'suspected_fall' ? '跌倒' : event.title}判断` : '视觉模型复核尚不支持确认';

export function adaptVerifiedResult(event: PublicDemoEvent, durationMs: number): DemoEvidence {
  if (!(Number.isFinite(event.startMs) && event.startMs >= 0 && event.endMs > event.startMs && event.endMs <= durationMs)) throw new Error('Invalid evidence interval');
  if (!event.referencedFrames.length || event.referencedFrames.some(frame => !Number.isFinite(frame.time_ms) || frame.time_ms < 0 || frame.time_ms > durationMs)) throw new Error('Invalid evidence frames');
  return {
    title: event.title, type: event.type, startMs: event.startMs, endMs: event.endMs,
    localCandidateTriggerSec: event.localCandidateTriggerSec,
    reviewVerdict: event.modelReviewVerdict, reviewReason: event.modelReviewReason,
    humanConfirmed: event.humanConfirmed,
    frames: event.referencedFrames.map(frame => ({ id: frame.frame_id, ptsMs: frame.time_ms, image: demoAsset(frame.image) })).sort((a, b) => a.ptsMs - b.ptsMs),
  };
}

export const demoRegistry: DemoCase[] = fixture.cases.map(item => ({
  id: item.id as DemoCaseId, title: item.title,
  videoSrc: demoAsset(item.videoSrc), posterSrc: demoAsset(item.posterSrc), durationMs: item.durationMs,
  previewStartSec: item.previewStartSec, previewEndSec: item.previewEndSec, disclaimer: item.disclaimer,
  modelResult: item.resultStatus === 'verified_model_export' && item.event
    ? { status: 'available', source: 'verified_model_export', result: adaptVerifiedResult(item.event, item.durationMs) }
    : { status: 'pending_model_fix', source: null, result: null },
}));

export const fallDemo = demoRegistry.find(item => item.id === 'fall')!;
if (fallDemo.modelResult.status !== 'available') throw new Error('Verified fall evidence is required');
export const fallEvidence = fallDemo.modelResult.result;
