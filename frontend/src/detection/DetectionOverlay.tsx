import { useCallback, useEffect, useRef, type RefObject } from 'react';
import { sampleDetection, videoContentRect } from './sampleDetection';
import { POSE_EDGES, POSE_JOINTS, type DetectionSequence } from './types';
import { useVideoFrame } from './useVideoFrame';
import './detection-overlay.css';

export function PersonBoundingBox() {
  return <><rect className="detection-box" rx="3" vectorEffect="non-scaling-stroke"/><path className="detection-corners" vectorEffect="non-scaling-stroke"/></>;
}

export function PoseSkeleton({ upperBody = false }: { upperBody?: boolean }) {
  return <g className="detection-skeleton">{POSE_EDGES.filter(([, b]) => !upperBody || b <= 10).map(([a, b]) => <line key={`${a}-${b}`} data-edge={`${a}-${b}`} vectorEffect="non-scaling-stroke"/>)}{POSE_JOINTS.filter(id => !upperBody || id <= 10).map(id => <circle key={id} data-joint={id} r={upperBody ? 6 : 3.1}/>)}</g>;
}

export function WristHighlight() {
  return <g className="wave-wrists">{['left', 'right'].map(side => <circle key={side} data-wrist={side} r="10" visibility="hidden" vectorEffect="non-scaling-stroke"/>)}</g>;
}

export function WristMotionTrail() {
  return <g className="wave-trajectories">{['left', 'right'].map(side => <polyline key={side} data-trail={side} visibility="hidden" vectorEffect="non-scaling-stroke"/>)}</g>;
}

export function WaveActionLabel() {
  return <g className="detection-risk wave-risk"><rect width="182" height="30" rx="15"/><path d="M12 18v-7m4 7V8m4 10v-7m4 7v-4M12 17c0 9 12 9 12 0M7 8l-2 6m24-6 2 6"/><text x="36" y="20">检测到挥手动作</text></g>;
}

export function RiskLabel() {
  return <g className="detection-risk"><rect width="142" height="34" rx="17"/><path d="M19 8 9 25h20Z M19 14v5 M19 22v1"/><text x="37" y="23">疑似跌倒</text></g>;
}

export function FireBoundingBox() {
  return <><rect className="detection-box fire-box" rx="4" vectorEffect="non-scaling-stroke"/><path className="detection-corners fire-corners" vectorEffect="non-scaling-stroke"/></>;
}

export function FireRiskLabel() {
  return <g className="detection-risk fire-risk"><rect width="154" height="36" rx="18"/><path d="M21 7c2 7-7 7-7 14a8 8 0 0 0 16 0c0-4-3-7-5-9 1 6-2 6-2 6 0-5-2-8-2-11Z"/><text x="39" y="25">疑似明火</text></g>;
}

export function DetectionHint({ text = '检测到人体姿态异常' }: { text?: string }) {
  return <g className="detection-hint"><rect width="256" height="30" rx="6"/><text x="128" y="21" textAnchor="middle">{text}</text></g>;
}

type Slot = {
  group: SVGGElement; box: SVGRectElement; corners: SVGPathElement; risk: SVGGElement;
  joints: SVGCircleElement[]; edges: SVGLineElement[];
  polygon: SVGPolygonElement | null;
  trails: SVGPolylineElement[]; wrists: SVGCircleElement[];
};
const attr = (element: Element, name: string, value: number | string) => element.setAttribute(name, String(value));

/** Accepts the same DetectionSequence for demos and future real per-run output.
 * The SVG uses source-video pixels inside a fitted/cropped content rectangle.
 * SVG nodes are allocated once; only attributes change as decoded frames arrive. */
export function DetectionOverlay({ videoRef, sequence, statusRef }: {
  videoRef: RefObject<HTMLVideoElement | null>; sequence: DetectionSequence; statusRef?: RefObject<HTMLElement | null>;
}) {
  const svgRef = useRef<SVGSVGElement>(null), worldRef = useRef<SVGGElement>(null);
  const hintRef = useRef<SVGGElement>(null), slots = useRef<Slot[]>([]);
  const width = sequence.video.width, height = sequence.video.height;
  const fire = sequence.target === 'fire';
  const wave = sequence.target === 'wave';
  const labelScaleRef = useRef(1);
  const waveLabelTopRef = useRef(6);
  const maxPeople = sequence.frames.reduce((max, frame) => Math.max(max, fire ? frame.objects?.length || 0 : frame.people.length), 1);
  const hasPolygon = fire && sequence.frames.some(frame => frame.objects?.some(object => object.polygon?.length));

  const draw = useCallback((seconds: number) => {
    const svg = svgRef.current, video = videoRef.current;
    if (!svg || !video) return;
    const frame = sampleDetection(sequence, seconds * 1000);
    // A new video must never inherit the previous video's annotations.
    const matches = video.readyState >= 2 && video.videoWidth === width && video.videoHeight === height;
    attr(svg, 'opacity', matches ? frame.opacity : 0);
    svg.dataset.timeMs = String(Math.round(seconds * 1000));
    const objects = fire ? frame.objects || [] : frame.people;
    svg.dataset.state = objects.length ? frame.state : 'empty';
    svg.dataset.frameIndex = frame.frameIndex === undefined ? '' : String(frame.frameIndex);
    if (wave && statusRef?.current) {
      const text = matches && objects.length && frame.labelVisible ? '检测到连续挥手动作' : '正在追踪手部动作';
      if (statusRef.current.textContent !== text) statusRef.current.textContent = text;
    }
    slots.current.forEach((slot, index) => {
      const person = matches ? objects[index] : undefined;
      attr(slot.group, 'visibility', person ? 'visible' : 'hidden');
      if (!person) return;
      const x = person.box.x * width, y = person.box.y * height;
      const w = person.box.width * width, h = person.box.height * height;
      Object.entries({ x, y, width: w, height: h }).forEach(([key, value]) => attr(slot.box, key, value));
      const corner = Math.min(14, w / 4, h / 4);
      attr(slot.corners, 'd', `M${x} ${y + corner}v${-corner}h${corner} M${x + w - corner} ${y}h${corner}v${corner} M${x + w} ${y + h - corner}v${corner}h${-corner} M${x + corner} ${y + h}h${-corner}v${-corner}`);
      const points = new Map(('keypoints' in person ? person.keypoints : []).filter(p => p.confidence >= sequence.minimumKeypointConfidence).map(p => [p.id, p]));
      slot.joints.forEach(node => {
        const point = points.get(Number(node.dataset.joint));
        attr(node, 'visibility', point ? 'visible' : 'hidden');
        if (point) { attr(node, 'cx', point.x * width); attr(node, 'cy', point.y * height); }
      });
      slot.edges.forEach(node => {
        const [aId, bId] = node.dataset.edge!.split('-').map(Number);
        const a = points.get(aId), b = points.get(bId);
        attr(node, 'visibility', a && b ? 'visible' : 'hidden');
        if (a && b) Object.entries({ x1: a.x * width, y1: a.y * height, x2: b.x * width, y2: b.y * height }).forEach(([key, value]) => attr(node, key, value));
      });
      const action = 'wave' in person ? person.wave : undefined;
      const active = wave ? !!action && frame.labelVisible : frame.labelVisible;
      attr(slot.risk, 'visibility', active ? 'visible' : 'hidden');
      if (wave) {
        // Keep the label readable with portrait footage, including the bars.
        const scale = labelScaleRef.current;
        attr(slot.risk, 'transform', `translate(${width / 2 - 91 * scale} ${Math.max(waveLabelTopRef.current, y - 36 * scale)}) scale(${scale})`);
        attr(slot.risk, 'opacity', action ? Math.max(0, Math.min(1, (seconds * 1000 - action.startMs) / 100, (action.endMs - seconds * 1000) / 100)) : 0);
      } else attr(slot.risk, 'transform', fire
        ? `translate(${x + w + 170 < width ? x + w + 10 : Math.max(6, x - 164)} ${Math.max(8, Math.min(height - 44, y + h / 2 - 18))})`
        : `translate(${Math.max(6, Math.min(width - 148, x))} ${Math.max(6, y - 43)})`);
      slot.trails.forEach(node => {
        const wrist = action?.wrists.find(w => w.side === node.dataset.trail);
        attr(node, 'visibility', wrist && wrist.trajectory.length >= 2 ? 'visible' : 'hidden');
        attr(node, 'points', wrist ? wrist.trajectory.map(p => `${p.x * width},${p.y * height}`).join(' ') : '');
      });
      slot.wrists.forEach(node => {
        const wrist = action?.wrists.find(w => w.side === node.dataset.wrist);
        const point = wrist && points.get(wrist.keypointId);
        attr(node, 'visibility', point ? 'visible' : 'hidden');
        if (point) { attr(node, 'cx', point.x * width); attr(node, 'cy', point.y * height); }
      });
      if (slot.polygon) {
        const polygon = 'polygon' in person ? person.polygon : undefined;
        attr(slot.polygon, 'visibility', polygon?.length ? 'visible' : 'hidden');
        if (polygon) attr(slot.polygon, 'points', polygon.map(p => `${p.x * width},${p.y * height}`).join(' '));
      }
    });
    if (hintRef.current) {
      attr(hintRef.current, 'visibility', !wave && matches && frame.labelVisible && objects.length ? 'visible' : 'hidden');
      attr(hintRef.current, 'transform', `translate(${(width - 256) / 2} ${height - 43})`);
    }
  }, [sequence, videoRef, width, height, fire, wave, statusRef]);

  useEffect(() => {
    const svg = svgRef.current, video = videoRef.current, world = worldRef.current;
    if (!svg || !video || !world) return;
    slots.current = Array.from(svg.querySelectorAll<SVGGElement>('[data-person-slot]')).map(group => ({
      group, box: group.querySelector<SVGRectElement>('.detection-box')!,
      corners: group.querySelector<SVGPathElement>('.detection-corners')!,
      risk: group.querySelector<SVGGElement>('.detection-risk')!,
      joints: Array.from(group.querySelectorAll<SVGCircleElement>('[data-joint]')),
      edges: Array.from(group.querySelectorAll<SVGLineElement>('[data-edge]')),
      polygon: group.querySelector<SVGPolygonElement>('.detection-polygon'),
      trails: Array.from(group.querySelectorAll<SVGPolylineElement>('[data-trail]')),
      wrists: Array.from(group.querySelectorAll<SVGCircleElement>('[data-wrist]')),
    }));
    const resize = () => {
      const viewport = svg.getBoundingClientRect(), box = video.getBoundingClientRect();
      const style = getComputedStyle(video);
      const rect = videoContentRect(width, height, box.width, box.height, style.objectFit, style.objectPosition);
      labelScaleRef.current = Math.min(.85, viewport.width / 210) / Math.max(.001, rect.width / width);
      if (wave) {
        const badge = video.parentElement?.querySelector('.av-real-footage')?.getBoundingClientRect();
        // Keep the media-time action label clear of the fixed source badge.
        waveLabelTopRef.current = badge ? Math.max(6, (badge.bottom + 6 - box.top - rect.y) * height / Math.max(1, rect.height)) : 6;
      }
      attr(svg, 'viewBox', `0 0 ${Math.max(1, viewport.width)} ${Math.max(1, viewport.height)}`);
      attr(world, 'transform', `translate(${box.left - viewport.left + rect.x} ${box.top - viewport.top + rect.y}) scale(${rect.width / width} ${rect.height / height})`);
      draw(video.currentTime);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(video); observer.observe(svg);
    video.addEventListener('loadedmetadata', resize);
    window.addEventListener('resize', resize);
    resize();
    return () => { observer.disconnect(); video.removeEventListener('loadedmetadata', resize); window.removeEventListener('resize', resize); slots.current = []; };
  }, [videoRef, sequence, width, height, draw]);
  useVideoFrame(videoRef, draw);

  return <svg ref={svgRef} className={`detection-overlay${fire ? ' fire-detection-overlay' : wave ? ' wave-detection-overlay' : ''}`} aria-hidden="true" focusable="false" opacity="0" data-target={sequence.target || 'person'} data-source={sequence.source.kind} data-source-basis={'basis' in sequence.source ? sequence.source.basis : 'inference'}>
    <g ref={worldRef}>{Array.from({ length: maxPeople }, (_, index) => <g key={index} data-person-slot={index} visibility="hidden">{fire ? <><FireBoundingBox/>{hasPolygon && <polygon className="detection-polygon" visibility="hidden"/>}<FireRiskLabel/></> : <><PersonBoundingBox/>{wave && <WristMotionTrail/>}<PoseSkeleton upperBody={wave}/>{wave ? <><WristHighlight/><WaveActionLabel/></> : <RiskLabel/>}</>}</g>)}<g ref={hintRef} visibility="hidden"><DetectionHint text={fire ? '已定位可见火焰线索' : undefined}/></g></g>
  </svg>;
}

/** Fire shares the media clock, source-pixel mapping and SVG lifecycle. */
export function FireDetectionOverlay(props: { videoRef: RefObject<HTMLVideoElement | null>; sequence: DetectionSequence }) {
  return <DetectionOverlay {...props}/>;
}

export function WaveDetectionOverlay(props: { videoRef: RefObject<HTMLVideoElement | null>; sequence: DetectionSequence; statusRef?: RefObject<HTMLElement | null> }) {
  return <DetectionOverlay {...props}/>;
}
