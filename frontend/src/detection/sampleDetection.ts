import type { DetectedPerson, DetectionFrame, DetectionSequence, Keypoint } from './types';

const mix = (a: number, b: number, ratio: number) => a + (b - a) * ratio;

function interpolatePerson(a: DetectedPerson, b: DetectedPerson, ratio: number, threshold: number): DetectedPerson {
  const points = new Map(b.keypoints.map(point => [point.id, point]));
  const keypoints: Keypoint[] = [];
  for (const point of a.keypoints) {
    const next = points.get(point.id);
    // Missing/uncertain joints never get invented or carried across a gap.
    if (!next || Math.min(point.confidence, next.confidence) < threshold) continue;
    keypoints.push({ id: point.id, x: mix(point.x, next.x, ratio), y: mix(point.y, next.y, ratio),
      confidence: Math.min(point.confidence, next.confidence) });
  }
  return { ...a, box: { x: mix(a.box.x, b.box.x, ratio), y: mix(a.box.y, b.box.y, ratio),
    width: mix(a.box.width, b.box.width, ratio), height: mix(a.box.height, b.box.height, ratio) }, keypoints };
}

export function sampleDetection(sequence: DetectionSequence, timeMs: number): DetectionFrame & { opacity: number } {
  const empty = { timeMs, people: [], objects: [], state: 'tracking' as const, labelVisible: false, opacity: 0 };
  const { startMs, endMs, fadeInMs, fadeOutMs } = sequence.display;
  if (!Number.isFinite(timeMs) || timeMs < startMs || timeMs >= endMs + fadeOutMs || !sequence.frames.length) return empty;
  const frames = sequence.frames;
  // Binary search also handles backwards seeks and loop resets without state.
  let lo = 0, hi = frames.length;
  while (lo < hi) { const mid = (lo + hi) >>> 1; if (frames[mid].timeMs <= timeMs) lo = mid + 1; else hi = mid; }
  if (!lo) return empty;
  const a = frames[lo - 1], b = frames[lo];
  // Frame-indexed annotations are held only for that decoded frame's duration.
  // Explicit validity prevents interpolation or carry-over across unknown gaps.
  if (a.validUntilMs !== undefined && timeMs >= a.validUntilMs) return empty;
  let people = a.people;
  if (sequence.sampleMode !== 'frame' && b && timeMs > a.timeMs) {
    if (b.timeMs - a.timeMs > sequence.maxInterpolationGapMs) return empty;
    const ratio = (timeMs - a.timeMs) / (b.timeMs - a.timeMs);
    people = a.people.flatMap(person => {
      const next = b.people.find(item => item.id === person.id);
      return next ? [interpolatePerson(person, next, ratio, sequence.minimumKeypointConfidence)] : [];
    });
  } else if (!b && a.validUntilMs === undefined && timeMs - a.timeMs > sequence.maxInterpolationGapMs) return empty;
  const opacity = Math.max(0, Math.min(1,
    fadeInMs ? (timeMs - startMs) / fadeInMs : 1,
    fadeOutMs ? (endMs + fadeOutMs - timeMs) / fadeOutMs : 1));
  return { ...a, timeMs, people, opacity };
}

/** Map source pixels into the video's CSS content box, including bars/cropping.
 * Values match object-fit with percentage/keyword object-position. */
export function videoContentRect(sourceWidth: number, sourceHeight: number, width: number, height: number,
  fit: string, position = '50% 50%') {
  if (Math.min(sourceWidth, sourceHeight, width, height) <= 0) return { x: 0, y: 0, width: 0, height: 0 };
  let sx = width / sourceWidth, sy = height / sourceHeight;
  if (fit !== 'fill') {
    const scale = fit === 'cover' ? Math.max(sx, sy) : fit === 'none' ? 1
      : fit === 'scale-down' ? Math.min(1, sx, sy) : Math.min(sx, sy);
    sx = sy = scale;
  }
  const offsets = position.trim().split(/\s+/);
  const fraction = (value: string) => value === 'left' || value === 'top' ? 0
    : value === 'right' || value === 'bottom' ? 1 : value?.endsWith('%') ? parseFloat(value) / 100 : .5;
  const renderedWidth = sourceWidth * sx, renderedHeight = sourceHeight * sy;
  return { x: (width - renderedWidth) * fraction(offsets[0]),
    y: (height - renderedHeight) * fraction(offsets[1] || '50%'), width: renderedWidth, height: renderedHeight };
}
