import { useEffect, useRef, useState, type RefObject } from 'react';
import { stamp } from './ui';

type Region = { id: string; label: string; x1: number; y1: number; x2: number; y2: number };
type Evt = { id: string; action: string; subject_id: string; target_id: string | null; start_ms: number; end_ms: number; evidence_ids: string[] };
type Entity = { id: string; label: string; appearance: string };
type Frame = { id: string; pts_ms: number; url: string };
type Memory = { job: { id: string }; frames: Frame[]; entities: Entity[]; evidence: { id: string; frame_ids: string[]; start_ms: number; end_ms: number }[] };
type Check = { hypothesis?: { action: string; target_id: string | null; reason: string; frame_ids: string[] } | null; verdict: string; reason: string; frame_ids: string[]; contradiction_frame_ids: string[]; subject_bbox?: { frame_id: string; x1: number; y1: number; x2: number; y2: number } | null };
type Entry = { original: Evt; status: string; machine: Check | null; human: { decision: string; note: string } | null; conflict: boolean; conflict_note: string };
type State = { manual_frame_ids?: string[]; revision: number; run_id: string | null; regions: Region[]; entries: Entry[]; entities: Entity[]; busy: boolean; history: { id: number; kind: string; created_at: string; decision?: string; note?: string }[] };
type Props = { initialOpen?: boolean; focusEvent?: { id: string; nonce: number } | null; videoId: string; memory: Memory | null; videoRef: RefObject<HTMLVideoElement | null>; running: boolean; onChange: () => Promise<void>; onSeek: (evidence: { id: string; start_ms: number; end_ms: number; frame_ids: string[] }) => void };

async function request<T>(url: string, body?: unknown): Promise<T> {
  const response = await fetch(url, body === undefined ? undefined : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error?.message || '保存失败，请刷新后再试。');
  return value as T;
}
const actions: Record<string, string> = { seen_at: '看见位于', place_on: '放在表面', pick_up: '拿起', put_in: '放入容器', take_out: '从容器取出', move: '移动', open: '打开', close: '关闭', explicit_exit: '离开整个画面', occluded: '被遮挡', unknown: '无法确定' };
const states: Record<string, string> = { ai_context: '上下文推测 · 未证实', pending: '候选推测 · 待复核', unreviewed: '待人工核对', human_confirm: '人工确认', human_correct: '人工纠正', human_reject: '人工否定', human_uncertain: '人工标记未知', ai_supported: 'AI复核支持 · 非人工确认', ai_refuted: 'AI复核反驳', ai_uncertain: 'AI仍无法确定' };

export function ReviewPanel({ initialOpen = true, focusEvent, videoId, memory, videoRef, running, onChange, onSeek }: Props) {
  const panelRef = useRef<HTMLDetailsElement>(null);
  const [state, setState] = useState<State | null>(null);
  const [regions, setRegions] = useState<Region[]>([]);
  const [preview, setPreview] = useState('');
  const [frameMs, setFrameMs] = useState(0);
  const [name, setName] = useState('');
  const [draft, setDraft] = useState<Region | null>(null);
  const drag = useRef<{ x: number; y: number } | null>(null);
  const [eventId, setEventId] = useState('');
  const [note, setNote] = useState('');
  const [action, setAction] = useState('seen_at');
  const [target, setTarget] = useState('');
  const [frameIds, setFrameIds] = useState<string[]>([]);
  const [focus, setFocus] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const url = `/api/videos/${videoId}`;
  const apply = (value: State) => { if (alive.current) { setState(value); setRegions(value.regions); } };
  useEffect(() => { request<State>(url + '/review').then(apply).catch(e => { if (alive.current) setError(e.message); }); }, [videoId, memory?.job.id]);
  useEffect(() => { if (!preview && memory?.frames[0]) { setPreview(memory.frames[0].url); setFrameMs(memory.frames[0].pts_ms); } }, [memory, preview]);
  const entry = state?.entries.find(e => e.original.id === eventId);
  const readable = (text: string) => text.replace(/rf_[a-f0-9]+|sf_[0-9]{12}|f[0-9]{6,}/g, id => { const frame = memory?.frames.find(f => f.id === id); return frame ? stamp(frame.pts_ms) : id; });
  const label = (id: string | null) => state?.entities.find(e => e.id === id)?.label || regions.find(r => r.id === id)?.label || '';
  const choose = (id: string) => {
    setEventId(id); setNote(''); setMessage(''); setError('');
    const item = state?.entries.find(e => e.original.id === id);
    if (item) {
      setAction(item.original.action); setTarget(item.original.target_id || '');
      setFrameIds(memory?.evidence.filter(e => item.original.evidence_ids.includes(e.id)).flatMap(e => e.frame_ids) || []);
    }
  };
  const reviewed = state?.entries.filter(e => e.human && e.human.decision !== 'reset').length || 0;
  useEffect(() => {
    if (!focusEvent || !state?.entries.some(e => e.original.id === focusEvent.id)) return;
    choose(focusEvent.id);
    if (panelRef.current) { panelRef.current.open = true; panelRef.current.scrollIntoView({ block: 'start' }); }
  }, [focusEvent?.id, focusEvent?.nonce, state?.run_id]);
  const nextUnreviewed = () => {
    const entries = state?.entries || []; const start = entries.findIndex(e => e.original.id === eventId);
    const ordered = [...entries.slice(start+1), ...entries.slice(0, start+1)];
    const next = ordered.find(e => !e.human || e.human.decision === 'reset');
    if (next) choose(next.original.id);
  };
  const perform = async (path: string, body: unknown, success: string) => {
    setBusy(true); setError(''); setMessage('');
    try {
      const result = await request<State>(url + path, body);
      if (!alive.current) return;
      apply(result); setMessage(success); await onChange();
    } catch (e) {
      if (alive.current) {
        setError((e as Error).message);
        // Failed model calls are audited too and advance the revision.
        try { apply(await request<State>(url + '/review')); await onChange(); } catch { /* preserve readable error */ }
      }
    } finally { if (alive.current) setBusy(false); }
  };
  const capture = () => {
    const video = videoRef.current;
    if (!video || video.readyState < 2 || !video.videoWidth) { setError('请先播放或定位视频，暂停在柜格清晰可见的位置。'); return; }
    video.pause();
    const canvas = document.createElement('canvas'); canvas.width = video.videoWidth; canvas.height = video.videoHeight;
    canvas.getContext('2d')?.drawImage(video, 0, 0);
    setPreview(canvas.toDataURL('image/jpeg', .85)); setFrameMs(Math.round(video.currentTime * 1000)); setError('');
  };
  const point = (e: React.PointerEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    return { x: Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)), y: Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height)) };
  };
  const drawing = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!drag.current) return;
    const p = point(e); const first = drag.current;
    setDraft({ id: 'r_' + crypto.randomUUID().replaceAll('-', '').slice(0, 12), label: '', x1: Math.min(first.x, p.x), x2: Math.max(first.x, p.x), y1: Math.min(first.y, p.y), y2: Math.max(first.y, p.y) });
  };
  const addRegion = () => {
    if (!draft || draft.x2 - draft.x1 < .005 || draft.y2 - draft.y1 < .005 || !name.trim()) { setError('请拖出有效矩形，并填写区域名称。'); return; }
    setRegions([...regions, { ...draft, label: name.trim() }]); setDraft(null); setName(''); setError('');
  };
  const locked = busy || running || !!state?.busy;
  const human = (decision: string) => state && entry && perform(`/review/${eventId}/human`, { run_id: state.run_id, expected_revision: state.revision, decision, note: note.trim(), ...(decision === 'correct' ? { action, target_id: target || null, frame_ids: frameIds } : {}) }, '人工复核已保存。原始AI输出保留，时间线和后续问答已更新。');
  // Always retain selected/model-cited evidence. Deduplicate optional frames by PTS so
  // a failed/repeated check cannot crowd the later contradiction out of the panel.
  const allFrames = memory?.frames || [];
  const lastPts = allFrames.reduce((n, f) => Math.max(n, f.pts_ms), 0);
  const mandatory = allFrames.filter(f => entry && (frameIds.includes(f.id) || state?.manual_frame_ids?.includes(f.id) || entry.machine?.frame_ids.includes(f.id) || entry.machine?.hypothesis?.frame_ids.includes(f.id) || entry.machine?.contradiction_frame_ids.includes(f.id) || entry.machine?.subject_bbox?.frame_id === f.id));
  const usedTimes = new Set(mandatory.map(f => f.pts_ms));
  const extraFrames: Frame[] = [];
  for (const f of [...allFrames].reverse()) {
    if (entry && !usedTimes.has(f.pts_ms) && (Math.abs(f.pts_ms - entry.original.start_ms) <= 3000 || Math.abs(f.pts_ms - entry.original.end_ms) <= 3000 || f.pts_ms >= lastPts - 3000)) {
      extraFrames.push(f); usedTimes.add(f.pts_ms);
    }
  }
  const shownFrames = [...mandatory, ...extraFrames.slice(0, Math.max(0, 48 - mandatory.length))].sort((a, b) => a.pts_ms - b.pts_ms);

  return <details ref={panelRef} className="review-panel" open={initialOpen}><summary>场景标定与人工复核</summary>
    <p className="muted">未复核的关键动作以候选推测展示，不用于确定包内状态。可请求模型结合前后画面解释未知动作。AI支持仍需人工核对；人工纠正会保留原始结果与历史。</p>
    {state?.entries.length ? <div className="review-controls"><span>人工已审查 {reviewed} / {state.entries.length} 条</span><button type="button" disabled={busy || running || reviewed === state.entries.length} onClick={nextUnreviewed}>下一条未审查事件</button></div> : null}
    {error && <p className="error" role="alert">{error}</p>}{message && <p className="review-success" role="status">{message}</p>}
    <details className="region-editor"><summary>① 标定桌面或柜格位置</summary>
      <p>将上方视频暂停在清晰画面，截取后拖动框选区域。名称统一按画面观看者的左右，例如“柜子第二层左格”。机位变化后需重新标定。</p>
      <button type="button" onClick={capture} disabled={locked}>使用播放器当前画面</button>
      {preview && <div className="region-canvas"><img src={preview} alt="用于标定的实际视频画面" /><svg aria-label="拖动框选位置区域" viewBox="0 0 1000 1000" preserveAspectRatio="none"
        onPointerDown={e => { if (locked) return; drag.current = point(e); e.currentTarget.setPointerCapture(e.pointerId); setDraft(null); }} onPointerMove={drawing} onPointerUp={e => { drawing(e); drag.current = null; }} onPointerCancel={() => { drag.current = null; setDraft(null); }}>
        {[...regions, ...(draft ? [draft] : [])].map((r, i) => <g key={r.id}><rect x={r.x1 * 1000} y={r.y1 * 1000} width={(r.x2 - r.x1) * 1000} height={(r.y2 - r.y1) * 1000} /><text x={r.x1 * 1000 + 5} y={r.y1 * 1000 + 30}>{r.label || `新区域 ${i + 1}`}</text></g>)}
      </svg></div>}
      <div className="review-controls"><input aria-label="区域名称" value={name} onChange={e => setName(e.target.value)} placeholder="例如：柜子第二层左格" maxLength={60} /><button type="button" onClick={addRegion} disabled={locked || !draft || regions.length >= 40}>添加框选区域</button></div>
      <ul className="region-list">{regions.map(r => <li key={r.id}>{r.label}<button type="button" disabled={locked} onClick={() => setRegions(regions.filter(x => x.id !== r.id))}>移除</button></li>)}</ul>
      <button type="button" disabled={locked || !state} onClick={() => state && void perform('/regions', { expected_revision: state.revision, frame_ms: frameMs, regions }, '区域标定已保存。原有事件不会被自动改写；可在纠正事件时选择新位置。')}>保存区域标定</button>
    </details>
    <div className="event-review"><h3>② 核对事件与证据</h3>
      <label htmlFor="review-event">选择待复核事件</label><select id="review-event" value={eventId} disabled={locked} onChange={e => choose(e.target.value)}><option value="">请选择事件</option>{state?.entries.map(item => <option key={item.original.id} value={item.original.id}>{stamp(item.original.start_ms)} · {label(item.original.subject_id)} · {actions[item.original.action]} · {states[item.status] || item.status}</option>)}</select>
      {entry && <>
        <p><strong>原始AI主张：</strong>{label(entry.original.subject_id)} · {actions[entry.original.action]} · {label(entry.original.target_id)}</p><p className="tag">{states[entry.status] || entry.status}</p>
        {entry.conflict && <p className="warning">{entry.conflict_note}</p>}
        <div className="review-controls"><button type="button" onClick={() => { const evidence = memory?.evidence.find(e => entry.original.evidence_ids.includes(e.id)); if (evidence) onSeek(evidence); }}>定位原始证据</button>
          <select aria-label="局部复核放大区域" value={focus} onChange={e => setFocus(e.target.value)}><option value="">完整画面复核</option>{state?.regions.map(r => <option key={r.id} value={r.id}>额外放大：{r.label}</option>)}</select>
          <button type="button" disabled={locked} onClick={() => state && void perform(`/review/${eventId}/verify`, { run_id: state.run_id, expected_revision: state.revision, focus_region_id: focus || null }, 'AI复核完成，请结合原视频进行人工确认。')}>{busy ? '处理中…' : 'AI 加密复核（1次请求）'}</button><button type="button" disabled={locked} onClick={() => state && void perform(`/review/${eventId}/verify`, { run_id: state.run_id, expected_revision: state.revision, focus_region_id: focus || null, mode: 'context' }, '上下文分析已保存。候选解释以推测展示，请核对证据与成立条件。')}>AI 上下文推测（1次请求）</button></div>
        <div className="review-controls"><button type="button" disabled={locked || !memory} onClick={() => state && void perform(`/review/${eventId}/sample`, { run_id: state.run_id, expected_revision: state.revision, center_ms: Math.round((videoRef.current?.currentTime || 0)*1000) }, '已补充播放器时刻前后的真实证据帧，未调用模型。')}>补充当前片段证据（免费）</button><button type="button" disabled={locked || !frameIds.length} onClick={() => setFrameIds([])}>清空已选证据</button><span>已选 {frameIds.length} / 16 帧</span></div>
        <p className="muted">AI复核将局部加密帧及后续画面发送至视觉服务，可能产生费用，不自动重试。人工操作不调用模型。</p>
        {entry.machine && <p className="warning">AI复核：{readable(entry.machine.reason)}</p>}{entry.machine?.hypothesis && <p className="warning">上下文候选解释：{actions[entry.machine.hypothesis.action]} {label(entry.machine.hypothesis.target_id)}。{readable(entry.machine.hypothesis.reason)}（仅针对所给局部及片尾采样帧，未确认全片连续状态；尚未证实，以有效时间线为准）</p>}
        <div className="review-frames">{shownFrames.map(f => <label key={f.id}><input type="checkbox" checked={frameIds.includes(f.id)} onChange={e => setFrameIds(e.target.checked ? [...frameIds, f.id] : frameIds.filter(id => id !== f.id))} /><span>{stamp(f.pts_ms)}</span><span className="review-frame-image"><img src={f.url} alt={`复核原始帧 ${stamp(f.pts_ms)}`} loading="lazy" />{entry.machine?.subject_bbox?.frame_id === f.id && <span className="bbox-overlay" style={{ left: `${entry.machine.subject_bbox.x1 * 100}%`, top: `${entry.machine.subject_bbox.y1 * 100}%`, width: `${(entry.machine.subject_bbox.x2 - entry.machine.subject_bbox.x1) * 100}%`, height: `${(entry.machine.subject_bbox.y2 - entry.machine.subject_bbox.y1) * 100}%` }} />}</span></label>)}</div>
        <p className="muted">请播放原视频核对完整过程。复核帧可勾选为纠正后的证据，最多16张；看不清时选择未知。</p>
        <p className="muted">只有在原片看清原事件时才确认。若动作或位置需要修改，使用“人工纠正”；采纳补充帧也需通过纠正保存。</p><label htmlFor="review-note">复核说明（必填）</label><textarea id="review-note" value={note} onChange={e => setNote(e.target.value)} maxLength={500} placeholder="例如：后续画面物品仍在桌上，无法支持入包。" />
        <div className="review-controls"><button type="button" disabled={locked || !note.trim()} onClick={() => void human('confirm')}>人工确认原事件</button><button type="button" disabled={locked || !note.trim()} onClick={() => void human('reject')}>否定原事件</button><button type="button" disabled={locked || !note.trim()} onClick={() => void human('uncertain')}>标记无法确定</button></div>
        <details><summary>纠正动作、位置或证据</summary><label htmlFor="review-action">纠正后的动作</label><select id="review-action" value={action} onChange={e => setAction(e.target.value)}>{Object.entries(actions).map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select>
          <label htmlFor="review-target">纠正后的位置或目标</label><select id="review-target" value={target} onChange={e => setTarget(e.target.value)}><option value="">目标不明／无目标</option>{state?.entities.filter(e => e.id !== entry.original.subject_id).map(e => <option key={e.id} value={e.id}>{e.label}</option>)}</select>
          <p className="muted">框选区域只代表画面位置，不能仅凭重叠确认进入容器。证据时间从上方勾选的原始帧生成。</p><button type="button" disabled={locked || !note.trim() || !frameIds.length || frameIds.length > 16} onClick={() => void human('correct')}>保存人工纠正</button></details>
        {entry.human && entry.human.decision !== 'reset' && <button type="button" disabled={locked || !note.trim()} onClick={() => void human('reset')}>撤销此事件人工修改</button>}
      </>}
    </div>
    <details className="review-history"><summary>③ 复核历史（{state?.history.length || 0}条）</summary>{state?.history.slice(-30).reverse().map(h => <p key={h.id}><time>{new Date(h.created_at).toLocaleString()}</time> · {({ regions: '场景标定', human: '人工复核', ai_started: 'AI复核开始', ai_result: 'AI复核结果', ai_failed: 'AI复核未完成', evidence_started: '补充画面开始', evidence_frames: '实际证据帧已补充', evidence_failed: '补充画面失败' } as Record<string, string>)[h.kind] || h.kind} · {h.note || h.decision || ''}</p>)}</details>
  </details>;
}
