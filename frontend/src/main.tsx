import { StrictMode, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './design-tokens.css';
import './style.css';
import './dashboard.css';
import { Shell } from './Shell';
import { AccountGate } from './AccountGate';
import { LanSetup } from './LanSetup';
import { LiveCameraPanel } from './LiveCameraPanel';
import { MemberManagement } from './MemberManagement';
import { Icon, mergedRanges, sizeLabel, stamp, durationLabel } from './ui';
import { EventTimeline, timelineRows, type EventGroup } from './EventTimeline';
import { LandingPage, type DemoVideo } from './LandingPage';
import { DeleteVideoDialog } from './DeleteVideoDialog';
import { SafetyPanel } from './SafetyPanel';
import { FlameScanSetting, VideoAnalysisControls, SafetyScanProgress, SafetyAnalysisPlan, analysisPath, planPath, jobMode, modeLabel, type AnalysisJob, type StartMode, type AnalysisMode, type FlameScan } from './VideoAnalysis';
import { eventClass, replayDescription } from './eventPresentation';
import { confidenceIndex, eventConfidence, displayClaim, type SourceClaim } from './confidence';
import './theme.css';
import './motion.css';
import './redesign.css';
import { introductionFromHash, type IntroductionPage } from './introduction';
import { viewFromUrl, inspectorFromUrl, type InspectorTab, type View } from './navigation';
import { InspectorTabs } from './VideoAnalysisWorkspace';
import { VideoAnalysisGuide, VideoRecognitionCapabilities } from './VideoWorkspaceGuide';
import { EvidenceTimebar } from './EvidenceTimebar';
import { seekVideo } from './seekVideo';
import './unified-workspace.css';
import './swiss-site.css';
import './swiss-workspace.css';
import './family-workspace.css';
import './product-refinements.css';
import './video-workspace-layout.css';
import { commonQuestions, eventTitle, familyClaim, familyUnknown, familyEntityLabel, unknownAnswer } from './familyPresentation';

type PublicError = { code: string; message: string; retryable: boolean };
type Video = { id: string; name: string; duration_ms: number; active_run?: string | null; read_only_demo?: boolean };
type Job = AnalysisJob & { id: string; video_id: string; status: string; stage: string; progress: number; is_mock: boolean; imported_recording?: boolean; error?: PublicError | null; failed_windows: { start_ms?: number; end_ms?: number; error?: PublicError }[]; completed_windows?: number; total_windows?: number; resumable?: boolean; pause_requested?: boolean; analyzed_ranges?: { start_ms: number; end_ms: number }[]; skipped_static_ranges?: { start_ms: number; end_ms: number }[]; activity_filter?: { enabled: boolean; skipped_windows: number; model_analyzed_windows: number; risk_followup: boolean } };
type Entity = { id: string; label: string; aliases: string[]; appearance: string; identity_status?: string; entity_type?: string };
type Evidence = { id: string; start_ms: number; end_ms: number; frame_ids: string[] };
type Frame = { id: string; pts_ms: number; url: string; kind?: string };
type Event = { id: string; start_ms: number; end_ms: number; subject_id: string; target_id: string | null; action: string; epistemic_type: string; rule_id?: string | null; evidence_ids: string[]; uncertainty_reasons: string[]; review_status?: string; model_score?: number | null };
type Memory = { entities: Entity[]; events: Event[]; evidence: Evidence[]; frames: Frame[]; job: Job; is_mock: boolean; event_groups?: EventGroup[] };
type Claim = SourceClaim;
type Answer = { status: string; observations: Claim[]; inferences: Claim[]; unknowns: string[]; evidence: Evidence[]; coverage_note: string; is_mock: boolean; candidates: Entity[]; answer_engine?: string; answer_style?: string; cached?: boolean; model_called?: boolean };
type Health = { cloud_mode?: boolean; analysis_ready: boolean; configuration_error: PublicError | null; is_mock: boolean; model: string; storage?: { free_bytes: number; reserve_bytes: number }; limits: { max_video_seconds: number | null; max_upload_mb: number | null } };
type Plan = { analysis_mode?: AnalysisMode; total_windows: number; max_windows_per_batch: number; daily_requests_remaining: number; estimated_batches: number | null; sampling_fps?: number; window_seconds?: number; activity_filter_enabled?: boolean; activity_filter_review_seconds?: number; activity_filter_person_review_seconds?: number };
type Replay = { entity: Entity; entries: { id: string; start_ms: number; end_ms: number; epistemic_type: string; event: Event; evidence_ids: string[]; condition?: string; rule_id?: string | null; source_event_ids?: string[] }[]; unknowns: string[] };

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, signal: init?.signal ?? AbortSignal.timeout(90000) }).catch(error => {
    if (error instanceof DOMException && error.name === 'TimeoutError') throw new Error('请求超时，请检查服务连接后重试。');
    throw error;
  });
  if (!response.ok) {
    let message = `请求失败（HTTP ${response.status}）`;
    try { message = (await response.json()).error?.message || message; } catch { /* readable fallback */ }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}
const statusName: Record<string, string> = { queued: '等待分析', running: '分析中', paused: '已保存进度', completed: '分析完成', partial: '部分完成', failed: '分析失败', interrupted: '分析中断', answered: '有证据回答', unknown: '无法确定', ambiguous: '需要消歧' };
const actionName: Record<string, string> = { seen_at: '出现在', place_on: '被放在', pick_up: '被拿起', put_in: '被放入', take_out: '被取出于', move: '移动到', open: '被打开', close: '被关闭', explicit_exit: '离开视野', occluded: '被遮挡', unknown: '动作无法确定', walk:'行走', sit_down:'坐下', stand_up:'起身', lie_down:'躺下', lying_on_floor:'躺卧于地面', fall:'出现疑似跌倒过程', climb:'攀爬', help_gesture:'挥手', no_person_visible:'所引用采样画面未见人物',smoke:'出现烟雾迹象',flame:'出现明火迹象',sparks:'出现火花／电弧迹象' };
const videoFromUrl = () => { const id = new URLSearchParams(location.search).get('video') || ''; return /^v_[A-Za-z0-9_-]{1,80}$/.test(id) ? id : ''; };

function App() {
  const [view, setView] = useState<View>(() => viewFromUrl(new URL(location.href)));
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>(() => inspectorFromUrl(new URL(location.href)));
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [mediaDuration, setMediaDuration] = useState(0);
  const seekOperation = useRef<AbortController | null>(null);
  const [introductionPage, setIntroductionPage] = useState<IntroductionPage>(() => introductionFromHash(location.hash));
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState('');
  const cameraVisited = useRef(view === 'camera');
  useEffect(() => {
    if (view === 'camera') { cameraVisited.current = true; return; }
    if (!cameraVisited.current || !health || health.cloud_mode) return;
    // The local worker stops after 30 seconds without status pings. Keep a
    // visited camera session alive across navigation without fetching frames.
    const controller = new AbortController();
    const ping = () => { void fetch('/api/camera/status', { cache: 'no-store', signal: controller.signal }).catch(() => {}); };
    ping();
    const timer = window.setInterval(ping, 10000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [view, !!health, health?.cloud_mode]);
  const [videos, setVideos] = useState<Video[]>([]);
  const [demos, setDemos] = useState<DemoVideo[]>([]);
  const [selected, setSelected] = useState<string>(() => {
    const id = new URLSearchParams(window.location.search).get('video') || '';
    return /^v_[A-Za-z0-9_-]{1,80}$/.test(id) ? id : '';
  });
  const selectedRef = useRef(selected); selectedRef.current = selected;
  useEffect(() => { const url = new URL(window.location.href); url.pathname = view === 'home' ? '/' : '/workspace'; if (view === 'home') url.searchParams.delete('view'); else url.searchParams.set('view', view); if (view === 'videos' && selected) url.searchParams.set('video', selected); else url.searchParams.delete('video'); if (view === 'videos') { url.searchParams.set('panel', inspectorTab); if (['#timeline','#ask','#analysis-tools'].includes(url.hash)) url.hash = ''; } else url.searchParams.delete('panel'); window.history.replaceState(null, '', url); }, [selected, view, inspectorTab]);
  useEffect(() => {
    const pop = () => { setView(viewFromUrl(new URL(location.href))); setSelected(videoFromUrl()); setInspectorTab(inspectorFromUrl(new URL(location.href))); setIntroductionPage(introductionFromHash(location.hash)); };
    window.addEventListener('popstate', pop); window.addEventListener('hashchange', pop);
    return () => { window.removeEventListener('popstate', pop); window.removeEventListener('hashchange', pop); };
  }, []);
  const [memory, setMemory] = useState<Memory | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [analysisAction, setAnalysisAction] = useState(false);
  const analysisActionRef = useRef(false);
  const [analysisMode, setAnalysisMode] = useState<StartMode>('safety');
  const [flameScan, setFlameScan] = useState<FlameScan>('normal');
  const [planRevision, setPlanRevision] = useState(0);
  const [planError, setPlanError] = useState('');
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [conversations, setConversations] = useState<Record<string, { id: string; question: string; answer: Answer }[]>>({});
  const [question, setQuestion] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Video | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState('');
  const [notice, setNotice] = useState('');
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadName, setUploadName] = useState('');
  const [dragging, setDragging] = useState(false);
  const uploadRequest = useRef<XMLHttpRequest | null>(null);
  const uploadController = useRef<AbortController | null>(null);
  const [eventSearch, setEventSearch] = useState('');
  const [eventCategory, setEventCategory] = useState<'all' | 'fall' | 'help_gesture' | 'flame' | 'other'>('all');
  const [hour, setHour] = useState('all');
  const [eventPage, setEventPage] = useState(1);
  const [jumpTime, setJumpTime] = useState('00:00:00');
  const [playbackTime, setPlaybackTime] = useState(0);
  const [asking, setAsking] = useState(false);
  const [activeEvidence, setActiveEvidence] = useState<Evidence | null>(null);
  const [seekMessage, setSeekMessage] = useState('');
  const [entityId, setEntityId] = useState('');
  const [replay, setReplay] = useState<Replay | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const recordingInput = useRef<HTMLInputElement>(null);
  const running = !!job && ['queued', 'running'].includes(job.status);
  const currentVideo = videos.find(v => v.id === selected);
  const planMode = currentVideo?.read_only_demo ? 'all' : analysisMode;
  const navigateIntroduction = (next: IntroductionPage) => {
    const url = new URL(location.href); url.pathname = '/'; url.searchParams.delete('video'); url.searchParams.delete('view'); url.searchParams.delete('panel'); url.hash = next === 'overview' ? '' : next;
    if (url.href !== location.href) window.history.pushState(null, '', url);
    setIntroductionPage(next); setView('home'); window.scrollTo({ top: 0, behavior: 'instant' });
  };
  const navigate = (next: View) => {
    if (next === 'home') { navigateIntroduction('overview'); return; }
    if (next === view) return;
    const url = new URL(location.href); url.pathname = '/workspace'; url.searchParams.set('view', next); if (next === 'videos') url.searchParams.set('panel', inspectorTab); else url.searchParams.delete('panel'); url.hash = '';
    if (next === 'videos' && selected) url.searchParams.set('video', selected); else url.searchParams.delete('video');
    window.history.pushState(null, '', url); setView(next); window.scrollTo({ top: 0, behavior: 'instant' });
  };
  const changeInspector = (next: InspectorTab) => {
    const url = new URL(location.href); url.searchParams.set('panel', next); url.hash = '';
    if (url.href !== location.href) window.history.pushState(null, '', url);
    setInspectorTab(next);
  };
  const requestDelete = (video: Video) => { setDeleteError(''); setDeleteTarget(video); };
  const confirmDelete = async () => {
    if (!deleteTarget || deleting) return;
    const target = deleteTarget; const player = videoRef.current; const previousTime = player?.currentTime || 0;
    setDeleting(true); setDeleteError('');
    // Release the browser's active range request before removing managed files on Windows.
    if (target.id === selected && player) { player.pause(); player.removeAttribute('src'); player.load(); }
    try {
      const result = await api<{ deleted: boolean; cleanup_pending: boolean; shared_files_retained: boolean }>(`/api/videos/${target.id}/delete`, { method: 'POST' });
      if (selectedRef.current === target.id) { selectedRef.current = ''; setSelected(''); setQuestion(''); }
      setDeleteTarget(null);
      setNotice(result.cleanup_pending ? '录像记录已删除，部分本地文件暂未清理完成。' : `已删除“${target.name}”。${result.shared_files_retained ? '其他录像仍在使用的共享原片已保留。' : ''}`);
      await refresh().catch(e => setError(`录像已删除，列表刷新失败：${e.message}`));
    } catch (e) {
      setDeleteError((e as Error).message);
      if (selectedRef.current === target.id && player) { player.src = `/media/videos/${target.id}`; player.addEventListener('loadedmetadata', () => { player.currentTime = previousTime; }, { once: true }); player.load(); }
    } finally { setDeleting(false); }
  };

  const refresh = async () => {
    // Camera availability must not wait for the recording/demo lists.
    setHealthError('');
    const healthRequest = api<Health>('/api/health').then(h => {
      setHealth(h);
      return h;
    }).catch(error => {
      setHealthError('运行环境读取失败，请重试。');
      throw error;
    });
    const videosRequest = api<{ videos: Video[] }>('/api/videos').then(v => {
      setVideos(v.videos);
      return v;
    });
    const demosRequest = api<{ demos: DemoVideo[] }>('/api/demos').then(d => setDemos(d.demos));
    const [h, v] = await Promise.all([healthRequest, videosRequest, demosRequest]);
    if (h.cloud_mode && !selectedRef.current && v.videos.length) setSelected(v.videos[0].id);
  };
  const loadMemory = async (id: string) => {
    const result = await api<Memory>(`/api/videos/${id}/events?grouped=true`);
    if (selectedRef.current === id) setMemory(result);
  };
  useEffect(() => { refresh().catch(e => setError(e.message)); }, []);
  useEffect(() => {
    seekOperation.current?.abort(); setSelectedEventId(null); setMediaDuration(0); setQuestion(''); setAsking(false);
    setMemory(null); setAnswer(null); setJob(null); setActiveEvidence(null); setSeekMessage(''); setError(''); setEntityId(''); setReplay(null); setPlan(null); setHour('all'); setEventSearch(''); setEventPage(1); setPlaybackTime(0); setJumpTime('00:00:00');
    setEventCategory('all'); setAnalysisMode('safety');
    setFlameScan('normal');
    if (!selected) return;
    let cancelled = false;
    api<Video & { latest_job: Job | null; metadata?: {case_type?: string} }>(`/api/videos/${selected}`).then(async detail => {
      if (cancelled) return;
      setJob(detail.latest_job);
      if (detail.latest_job?.analysis_mode === 'objects' || (!detail.latest_job?.analysis_mode && detail.metadata?.case_type === 'long_video')) { setAnalysisMode('objects'); }
      if (detail.active_run) await loadMemory(selected);
    }).catch(e => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [selected]);
  useEffect(() => {
    setPlan(null); setPlanError('');
    if (!selected) return;
    let cancelled = false;
    api<Plan>(planPath(selected, planMode)).then(p => { if (!cancelled) setPlan({ ...p, analysis_mode: planMode }); }).catch(e => { if (!cancelled) setPlanError(e.message); });
    return () => { cancelled = true; };
  }, [selected, planMode, planRevision]);
  useEffect(() => {
    if (!selected || !entityId || !memory) { setReplay(null); return; }
    let cancelled = false;
    api<Replay>(`/api/videos/${selected}/replay?entity_id=${encodeURIComponent(entityId)}`).then(result => { if (!cancelled) setReplay(result); }).catch(e => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [selected, entityId, memory]);
  useEffect(() => {
    if (!job || !running) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    let lastResultRefresh = 0;
    const poll = async () => {
      try {
        const next = await api<Job>(`/api/jobs/${job.id}`);
        if (cancelled || selectedRef.current !== next.video_id) return;
        setJob(next);
        if (!['running', 'queued'].includes(next.status)) {
          if (['completed', 'partial', 'paused'].includes(next.status)) await loadMemory(next.video_id);
          if (next.error && next.status !== 'paused') setError(next.error.message);
          if (!cancelled) setPlanRevision(value => value + 1);
          await refresh();
          return;
        }
        if (Date.now() - lastResultRefresh > 4000) {
          lastResultRefresh = Date.now();
          // Some jobs publish records incrementally. No result is also a valid
          // state while the first segment is being processed.
          await loadMemory(next.video_id).catch(() => {});
          if (cancelled) return;
        }
        timer = setTimeout(poll, 800);
      } catch (e) { if (!cancelled) setError((e as Error).message); }
    };
    timer = setTimeout(poll, 300);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [job?.id, running]);

  const upload = async (file?: File) => {
    if (!file || busy) return;
    if (!/\.mp4$/i.test(file.name)) { setError('请选择 MP4 格式的录像。'); return; }
    if (health?.limits.max_upload_mb != null && file.size > health.limits.max_upload_mb * 1024 ** 2) { setError(`文件超过 ${sizeLabel(health.limits.max_upload_mb * 1024 ** 2)} 上传上限。`); return; }
    if (health?.storage && file.size > health.storage.free_bytes - health.storage.reserve_bytes) { setError('本机数据盘空间不足，请释放空间或配置其他数据目录后再上传。'); return; }
    setBusy(true); setError(''); setUploadProgress(0); setUploadName(file.name);
    const controller = new AbortController(); uploadController.current = controller;
    let chunkId = '';
    try {
      let result: { video_id: string };
      if (health?.cloud_mode && file.size > 16*1024**2) {
        const started = await api<{upload_id: string; chunk_bytes: number}>('/api/uploads', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({filename:file.name.slice(0,150),size:file.size})});
        chunkId = started.upload_id;
        for (let offset=0; offset<file.size;) {
          if (controller.signal.aborted) throw new Error('已取消上传。');
          const end = Math.min(file.size,offset+Math.min(started.chunk_bytes,4*1024**2));
          const received = await api<{received:number}>(`/api/uploads/${chunkId}/chunks?offset=${offset}`, {method:'POST', headers:{'Content-Type':'video/mp4'}, body:file.slice(offset,end), signal:controller.signal});
          if (received.received !== end) throw new Error('上传片段确认不一致，请重新上传。');
          offset = end; setUploadProgress(Math.min(99,Math.round(offset/file.size*100)));
        }
        setUploadProgress(100);
        result = await api<{video_id:string}>(`/api/uploads/${chunkId}/complete`,{method:'POST'});
        chunkId = '';
      } else {
      result = await new Promise<{ video_id: string }>((resolve, reject) => {
        const xhr = new XMLHttpRequest(); uploadRequest.current = xhr;
        xhr.open('POST', '/api/videos/upload');
        xhr.setRequestHeader('Content-Type', 'video/mp4');
        xhr.setRequestHeader('X-Video-Name', encodeURIComponent(file.name));
        xhr.upload.onprogress = e => { if (e.lengthComputable) setUploadProgress(Math.round(e.loaded / e.total * 100)); };
        xhr.onload = () => {
          try { const body = JSON.parse(xhr.responseText); if (xhr.status >= 200 && xhr.status < 300) resolve(body); else reject(new Error(body.error?.message || '上传失败，请重试。')); }
          catch { reject(new Error('上传响应无效，请检查服务状态。')); }
        };
        xhr.onerror = () => reject(new Error('上传连接中断，请检查本地服务后重试。'));
        xhr.onabort = () => reject(new Error('已取消上传。'));
        xhr.send(file);
      });
      }
      await refresh(); setSelected(result.video_id);
    } catch (e) {
      if (chunkId) await api(`/api/uploads/${chunkId}/cancel`,{method:'POST'}).catch(()=>{});
      setError(controller.signal.aborted ? '已取消上传。' : (e as Error).message);
    }
    finally { setBusy(false); uploadRequest.current = null; uploadController.current = null; }
  };
  const changeAnalysisMode = (mode: StartMode) => {
    setAnalysisMode(mode);
    setEntityId(''); setReplay(null); setAnswer(null); setEventPage(1);
  };
  const chooseFlameScan = (scan: FlameScan) => {
    setFlameScan(scan);
    if (analysisMode !== 'safety') changeAnalysisMode('safety');
  };
  const analyze = async (mode: AnalysisMode, resume = false) => {
    if (!selected || running || analysisActionRef.current || !health?.analysis_ready) return;
    analysisActionRef.current = true;
    const id = selected;
    setError(''); setAnswer(null); setAnalysisAction(true);
    try {
      const demo = !!currentVideo?.read_only_demo;
      const next = await api<Job>(analysisPath(id, mode, resume, demo, flameScan), { method: 'POST' });
      if (selectedRef.current === id) {
        if (demo) { await refresh(); setSelected(next.video_id); setNotice('已创建你的分析副本，正在重新识别视频。原展示成果保留；新分析计入每日调用上限。'); }
        else { setJob(next); setPlanRevision(value => value + 1); }
      }
    }
    catch (e) { if (selectedRef.current === id) setError((e as Error).message); }
    finally { analysisActionRef.current = false; setAnalysisAction(false); }
  };
  const pauseAnalysis = async () => {
    if (!selected || !running || analysisActionRef.current || job?.pause_requested) return;
    analysisActionRef.current = true;
    const id = selected;
    setError(''); setAnalysisAction(true);
    try { const next = await api<Job>(`/api/videos/${id}/pause`, { method: 'POST' }); if (selectedRef.current === id) { setJob(next); if (next.status === 'paused') await loadMemory(id); } }
    catch (e) { if (selectedRef.current === id) setError((e as Error).message); }
    finally { analysisActionRef.current = false; setAnalysisAction(false); }
  };
  const ask = async (event: React.FormEvent) => {
    event.preventDefault(); if (!question.trim() || asking || !memory || running) return;
    const id = selected;
    const submittedQuestion = question.trim();
    setAsking(true); setError('');
    try {
      const result = await api<Answer>(`/api/videos/${id}/query`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: question.trim(), entity_id: entityId || undefined, use_model: true }) });
      setConversations(current => ({ ...current, [id]: [...(current[id] || []), { id: crypto.randomUUID(), question: submittedQuestion, answer: result }] }));
      if (selectedRef.current === id) setAnswer(result);
    } catch (e) { if (selectedRef.current === id) setError((e as Error).message); }
    finally { if (selectedRef.current === id) setAsking(false); }
  };
  useEffect(() => () => { seekOperation.current?.abort(); }, [selected, view]);
  const seekTo = async (timeMs: number) => {
    seekOperation.current?.abort();
    const controller = new AbortController(); seekOperation.current = controller;
    const player = videoRef.current; const id = selected;
    if (!player) { setSeekMessage('请先选择可播放的录像。'); return; }
    setPlaybackTime(Math.max(0, timeMs / 1000));
    setSeekMessage('正在定位视频…');
    try {
      const actual = await seekVideo(player, timeMs, controller.signal);
      if (!controller.signal.aborted && selectedRef.current === id) {
        setPlaybackTime(actual);
        setSeekMessage(Math.abs(actual - timeMs / 1000) < .25 ? `已定位至 ${stamp(timeMs)}` : '已定位到附近画面。');
      }
    } catch (cause) { if (!controller.signal.aborted && selectedRef.current === id) setSeekMessage((cause as Error).message); }
  };
  const seek = (evidence: Evidence, eventId?: string) => {
    setActiveEvidence(evidence);
    const event = memory?.events.find(event => event.id === (eventId || selectedEventId) && event.evidence_ids.includes(evidence.id))
      || memory?.events.find(event => event.evidence_ids.includes(evidence.id));
    setSelectedEventId(event?.id || null);
    void seekTo(evidence.start_ms);
  };
  const selectEvent = (event: Event) => {
    setSelectedEventId(event.id);
    const evidence = memory?.evidence.find(item => event.evidence_ids.includes(item.id));
    if (evidence) seek(evidence, event.id); else { setActiveEvidence(null); void seekTo(event.start_ms); }
  };
  const references = (ids: string[]) => ids.map(id => {
    const evidence = memory?.evidence.find(e => e.id === id);
    return evidence ? <button className="reference" key={id} onClick={() => seek(evidence)} aria-pressed={activeEvidence?.id === id} aria-label={`查看视频片段 ${stamp(evidence.start_ms)}`}>{stamp(evidence.start_ms)}–{stamp(evidence.end_ms)} <Icon name="arrow-up-right" size={13}/></button> : null;
  });
  const label = (id: string | null) => familyEntityLabel(memory?.entities.find(e => e.id === id)?.label || '');
  const entityType = (id: string) => memory?.entities.find(e => e.id === id)?.entity_type;

  const confidenceContext = useMemo(() => confidenceIndex(memory), [memory]);
  const levels = useMemo(() => new Map((memory?.events || []).map(e => [e.id, eventConfidence(e,confidenceContext)])), [memory,confidenceContext]);
  const confidenceOf = (e: Event) => levels.get(e.id) ?? eventConfidence(e,confidenceContext);
  const visibleEvents = (memory?.events || []).filter(e => confidenceOf(e) !== 'hidden' && eventClass(e,confidenceContext.entities) !== 'support');
  const scopedEvents = visibleEvents;
  const visibleSubjectIds = new Set(visibleEvents.map(e => e.subject_id));
  const selectableEntities = (memory?.entities || []).filter(e => visibleSubjectIds.has(e.id));
  const answerCandidates = (answer?.candidates || []).filter(e => visibleSubjectIds.has(e.id));
  const filteredEvents = scopedEvents.filter(e => (eventCategory === 'all' || (eventCategory === 'other' ? !['fall','help_gesture','flame'].includes(e.action) : e.action === eventCategory)) && (hour === 'all' || (e.start_ms < (Number(hour)+1)*3600000 && e.end_ms >= Number(hour)*3600000)) && `${label(e.subject_id)} ${label(e.target_id)} ${eventTitle(e.action)}`.toLocaleLowerCase().includes(eventSearch.toLocaleLowerCase()));
  const rows = timelineRows(filteredEvents, memory?.event_groups || [], true);
  const replayEntries = (replay?.entries || []).flatMap(entry => {
    if (eventClass(entry.event,confidenceContext.entities) === 'support') return [];
    const level = entry.rule_id === 'conditional_containment_v1'
      ? displayClaim({text:entry.condition || '',event_ids:entry.source_event_ids,evidence_ids:entry.evidence_ids,rule_id:entry.rule_id},confidenceContext,true)?.confidence || 'hidden'
      : confidenceOf(entry.event);
    return level === 'hidden' ? [] : [{...entry,confidence:level}];
  });
  const replayEntities = selectableEntities;
  const directReplay = replayEntries.filter(entry => entry.rule_id !== 'conditional_containment_v1');
  const replayById = new Map(directReplay.map(entry => [entry.event.id,entry]));
  const replayDisplayEntries = [
    ...timelineRows(directReplay.map(entry => entry.event), memory?.event_groups || [], true).map(row => {
      const sources = row.members.map(event => replayById.get(event.id)!);
      return {...sources[0],epistemic_type:sources.some(entry => entry.epistemic_type === 'inferred') ? 'inferred' : sources[0].epistemic_type,confidence:sources.some(entry => entry.confidence === 'medium') ? 'medium' as const : sources[0].confidence,end_ms:Math.max(...sources.map(entry => entry.end_ms)),
        evidence_ids:[...new Set(sources.flatMap(entry => entry.evidence_ids))],groupKind:row.kind,sourceEntries:sources.length > 1 ? sources : []};
    }),
    ...replayEntries.filter(entry => entry.rule_id === 'conditional_containment_v1').map(entry => ({...entry,groupKind:'single' as const,sourceEntries:[]})),
  ].sort((a,b) => a.start_ms-b.start_ms || a.end_ms-b.end_ms);
  const observations = (answer?.observations || []).flatMap(c => { const view=displayClaim(c,confidenceContext);return view ? [view] : []; });
  const inferences = (answer?.inferences || []).flatMap(c => { const view=displayClaim(c,confidenceContext,true);return view ? [view] : []; });
  const answerUnknowns = [...new Set((answer?.unknowns || []).map(familyUnknown))];
  if (answer && !observations.length && !inferences.length && !answerUnknowns.length) answerUnknowns.push(unknownAnswer);
  const pageCount = Math.max(1, Math.ceil(rows.length / 20));
  const visibleRows = rows.slice((Math.min(eventPage, pageCount)-1)*20, Math.min(eventPage, pageCount)*20);
  const ranges = mergedRanges(job?.analyzed_ranges || []);
  const staticRanges = mergedRanges(job?.skipped_static_ranges || []);
  const jump = () => {
    const parts = jumpTime.split(':').map(Number); const seconds = parts[0]*3600 + (parts[1] || 0)*60 + (parts[2] || 0);
    if (!currentVideo || !Number.isFinite(seconds) || seconds*1000 >= currentVideo.duration_ms) { setError('跳转时间需要在当前录像范围内。'); return; }
    setActiveEvidence(null); setSelectedEventId(null); void seekTo(seconds * 1000);
  };

  const configurationNotice = health && !health.analysis_ready ? <><span>视频分析服务暂未就绪，可先导入和播放录像。</span><button className="text-button" onClick={() => refresh().catch(e => setError(e.message))}>重新检查连接</button></> : null;

  return <Shell videos={videos} selected={selected} onSelect={id => { setSelected(id); navigate('videos'); }} ready={!!health?.analysis_ready} loading={!health && !healthError} serviceError={!!healthError} localCamera={!health?.cloud_mode} model={health?.model || ''} view={view} introductionPage={introductionPage} onIntroductionNavigate={navigateIntroduction} onNavigate={navigate} onDelete={requestDelete} busyVideoId={running ? selected : deleting ? deleteTarget?.id : undefined}>
    {notice && <div className="global-notice" role="status"><span>{notice}</span><button className="text-button" onClick={() => setNotice('')}>收起</button></div>}
    {view === 'home' ? <LandingPage page={introductionPage} onPageChange={navigateIntroduction} onEnter={() => navigate('videos')} onCamera={() => navigate('camera')} demos={demos} onOpenDemo={id => { setSelected(id); navigate('videos'); }}/>: <>
    {(health?.is_mock || memory?.is_mock || job?.is_mock || answer?.is_mock) && <div className="mock-banner" role="alert">测试模拟模式：事件由测试适配器提供，未执行真实AI识别，不可用作产品效果演示。</div>}
    <main id="main-content" className={view === 'members' ? 'members-workspace' : view === 'camera' ? 'camera-workspace' : `video-workspace unified-workspace ${selected ? 'is-video-selected' : 'is-video-empty'}${videos.length === 0 ? ' is-empty-library' : ''}`} aria-label={view === 'members' ? '家庭成员管理' : view === 'camera' ? '实时摄像头工作台' : 'AI 视频分析工作台'} tabIndex={-1}>
      {view === 'members' ? <MemberManagement cloud={health?.cloud_mode} ready={health !== null}/> : view === 'camera' ? <>
        {health?.cloud_mode ? <div className="configuration"><span>当前在线环境不支持连接本机摄像头。请在本机启动服务后使用实时摄像头，或导入视频进行分析。</span><button className="text-button" onClick={() => navigate('videos')}>导入视频</button></div> : <LiveCameraPanel ready={health !== null} configurationError={healthError} onRetry={() => { void refresh().catch(e => setError(e.message)); }} connectionActions={health && <LanSetup />} />}
        {!health?.cloud_mode && <p className="privacy">在 <a href="/workspace?view=members" onClick={event => { event.preventDefault(); navigate('members'); }}>家庭成员</a> 添加家人照片，安全提醒中可显示可能对应的家人。</p>}
        <p className="privacy">切换导航不会停止摄像头。需要结束实时看护时，请点击“停止预览”。</p>
      </> : <>
      <div className="workspace-notices">
        {health?.cloud_mode && <p className="cloud-demo-note">在线体验：内置示例为已保存的真实模型分析，可直接回看和提问；上传新视频后可触发新的模型分析。上传录像仅对当前浏览器访客可见，请勿上传敏感画面。若内嵌页面无法保留会话，请通过平台提供的独立页面入口打开。</p>}
        {error && <div className="error" role="alert">{error}<button className="text-button" onClick={() => setError('')}>收起</button></div>}
        {configurationNotice && <details className="configuration"><summary>录像云分析尚未就绪 · 上传与播放可用</summary><div>{configurationNotice}</div></details>}
      </div>
      <div className="analysis-layout">
        <div className="analysis-player-column">
        <section className="panel video-panel" id="watch" aria-label="当前录像播放器" tabIndex={0}>
          <input ref={recordingInput} id="recording-upload" aria-label="上传 MP4" name="recording" type="file" accept="video/mp4,.mp4" hidden disabled={busy} onChange={e => { void upload(e.target.files?.[0]); e.target.value = ''; }} />
          <div className="recording-toolbar">
            <div className="video-select"><label htmlFor="video-select">当前视频</label><select id="video-select" value={selected} disabled={videos.length === 0} onChange={e => setSelected(e.target.value)}><option value="">{videos.length ? '选择已上传视频' : '暂无录像，请先导入'}</option>{videos.map(v => <option value={v.id} key={v.id}>{v.name} · {stamp(v.duration_ms)}</option>)}</select></div>
            <div className="video-panel-actions"><button type="button" className="recording-import" disabled={busy} onClick={() => recordingInput.current?.click()}><Icon name="upload" size={16}/>{busy ? '正在导入…' : '导入新录像'}</button>{currentVideo && <button type="button" className="delete-video-button" disabled={running || deleting || busy || currentVideo.read_only_demo} onClick={() => requestDelete(currentVideo)}><Icon name="trash" size={14}/><span>删除视频</span></button>}</div>
          </div>
      {busy && <div className="upload-progress" aria-live="polite"><div><strong>{uploadName}</strong><span>{uploadProgress < 100 ? `已上传 ${uploadProgress}%` : '文件传输完成，正在校验并准备播放。长录像可能需要一些时间。'}</span>{uploadProgress < 100 && <button className="text-button" onClick={() => { uploadController.current?.abort(); uploadRequest.current?.abort(); }}>取消上传</button>}</div><progress aria-label="上传进度" max={100} value={uploadProgress} /></div>}
          {selected ? <div className="recording-stage"><div className="recording-player" aria-label="录像画面"><video key={`video-${selected}`} ref={videoRef} controls playsInline preload="metadata" onLoadedMetadata={e => setMediaDuration(e.currentTarget.duration)} onTimeUpdate={e => setPlaybackTime(e.currentTarget.currentTime)} src={`/media/videos/${selected}`} onError={() => setError('视频播放器加载失败，请检查本地媒体服务或重新上传。')} /></div></div> : <button type="button" className={`empty-video upload-dropzone ${dragging ? 'is-dragging' : ''}`} disabled={busy} aria-label="导入居家录像" aria-busy={busy} onClick={() => recordingInput.current?.click()} onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = busy ? 'none' : 'copy'; if (!busy) setDragging(true); }} onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false); }} onDrop={e => { e.preventDefault(); setDragging(false); void upload(e.dataTransfer.files[0]); }}><span className="empty-upload-icon"><Icon name="upload" size={26}/></span><strong>从一段居家录像开始</strong><span className="empty-upload-description">导入录像，留意跌倒、连续挥手和明火，及时获取安全提醒。</span><span className="empty-upload-action">{busy ? (uploadProgress < 100 ? `正在上传 ${uploadProgress}%` : '正在准备录像…') : '导入居家录像'}</span>{videos.length > 0 && <span className="empty-video-selection-hint">也可以从左侧录像列表或顶部下拉框选择已有录像。</span>}<small>{dragging ? '松开即可导入录像' : `点击选择或拖入 MP4 · ${health ? health.limits.max_video_seconds === null ? '不设大小与时长上限' : `最长${durationLabel(health.limits.max_video_seconds*1000)}` : '正在读取上传设置…'}`}</small></button>}
          {currentVideo && <div className="recording-controls"><div className="playback-clock"><Icon name="clock" size={15} /><span>{stamp(playbackTime*1000)}</span><span>/ {stamp(currentVideo.duration_ms)}</span></div><div className="time-jump"><input aria-label="跳转录像时间" type="time" step="1" value={jumpTime} onChange={e => setJumpTime(e.target.value)} /><button onClick={jump}>跳转</button></div></div>}

          {currentVideo && <EvidenceTimebar durationSec={mediaDuration || currentVideo.duration_ms / 1000} timeSec={playbackTime} ranges={visibleEvents.flatMap(event => event.evidence_ids.flatMap(id => { const evidence = memory?.evidence.find(item => item.id === id); return evidence ? [{ id: `${event.id}:${id}`, startMs: evidence.start_ms, endMs: evidence.end_ms, label: eventTitle(event.action), selected: selectedEventId === event.id }] : []; }))} coverage={ranges} onSeek={time => { setActiveEvidence(null); setSelectedEventId(null); void seekTo(time * 1000); }} maxMarkers={3} onSelect={id => { const event = visibleEvents.find(item => id.startsWith(`${item.id}:`)); if (event) { changeInspector('events'); selectEvent(event); } }} label="工作台视频时间定位"/>}
          {selected && <p className="workspace-seek-status" role="status">{seekMessage}</p>}
          {selected && <div className="workspace-analysis-area">
            <VideoAnalysisControls mode={analysisMode} onModeChange={changeAnalysisMode} job={job} pending={analysisAction} ready={!!health?.analysis_ready} demo={!!currentVideo?.read_only_demo} onStart={() => void analyze(analysisMode)} onResume={() => void analyze(jobMode(job), true)} onPause={() => void pauseAnalysis()} />
            {!currentVideo?.read_only_demo && <details className="analysis-options"><summary>更多分析选项</summary><FlameScanSetting value={flameScan} onChange={chooseFlameScan} disabled={running || analysisAction} /></details>}
            <SafetyAnalysisPlan plan={plan?.analysis_mode === planMode ? plan : null} />
            {planError && <p className="muted">暂时无法读取分析额度，开始分析时会再次检查。</p>}
            {staticRanges.length > 0 && <p className="analysis-task-note">已检查画面变化较少的时段。</p>}
          </div>}

        </section>
        </div>
        {!selected ? <VideoAnalysisGuide/> : <aside className="analysis-inspector" aria-label="当前视频上下文工具">
          <InspectorTabs active={inspectorTab} onChange={changeInspector}/>
          <div className="workspace-inspector-scroll">
            <section id="inspector-panel-events" role="tabpanel" aria-labelledby="inspector-tab-events" hidden={inspectorTab !== 'events'} tabIndex={0}>
              <div className="inspector-section-heading"><h2>事件提醒</h2>{memory && <span>{rows.length} 项相关活动</span>}</div>
            {job && ['completed','partial','paused','failed','interrupted'].includes(job.status) && <section className="analysis-result-summary" aria-label="分析结果摘要"><h2>{statusName[job.status] || '分析结果'}</h2><p>{memory ? `已整理 ${timelineRows(visibleEvents, memory.event_groups || [], true).length} 项相关活动，可查看提醒或提问。` : '暂时没有可展示的结果，可以重新开始分析。'}</p>{job.status !== 'completed' && <p className="warning">部分片段尚未分析完成，当前结果可能有遗漏。{job.resumable ? '可继续上次任务。' : '请稍后重试。'}</p>}{memory && <div className="summary-actions"><button className="text-button" onClick={() => changeInspector('events')}>查看事件提醒</button><button className="text-button" onClick={() => changeInspector('qa')}>询问录像内容</button></div>}</section>}
              {running && <div className="inspector-analysis-state" role="status"><Icon name="activity" size={20}/><div><strong>{jobMode(job) === 'objects' ? '正在整理人物和物品的踪迹' : '正在留意录像中的安全情况'}</strong><p>{jobMode(job) === 'objects' ? '相关活动会陆续显示在目标追踪中。' : '识别到的事件会陆续显示。'}</p>{jobMode(job) !== 'objects' && <SafetyScanProgress safety={job?.safety}/>}</div></div>}
              <nav className="event-categories" aria-label="事件分类">{([['all','全部事件'],['fall','跌倒提醒'],['help_gesture','挥手动作'],['flame','明火提醒'],...(visibleEvents.some(e => !['fall','help_gesture','flame'].includes(e.action)) ? [['other','其他事件']] : [])] as [typeof eventCategory,string][]).map(([key,text]) => <button key={key} aria-pressed={eventCategory === key} onClick={() => { setEventCategory(key); setEventPage(1); }}>{text}</button>)}</nav>
              <section className="timeline timeline-panel" id="timeline" aria-label="事件时间线">
                {memory && <div className="event-tools"><label className="event-search"><Icon name="search" size={16}/><input aria-label="搜索事件物品或动作" value={eventSearch} onChange={e => { setEventSearch(e.target.value); setEventPage(1); }} placeholder="搜索人物、物品或动作"/></label>{(currentVideo?.duration_ms || 0) > 3600000 && <select aria-label="按录像小时筛选" value={hour} onChange={e => { setHour(e.target.value); setEventPage(1); }}><option value="all">全部时段</option>{Array.from({length:Math.ceil((currentVideo?.duration_ms || 0)/3600000)},(_,i) => <option key={i} value={i}>{i}:00–{i+1}:00</option>)}</select>}</div>}
                {!memory ? <p className="empty">{running ? '分析仍在进行，请稍候。' : '完成视频分析后，识别到的安全事件将在这里展示。'}</p> : !filteredEvents.length ? <p className="empty">{visibleEvents.length ? '当前筛选没有匹配事件。' : '暂未识别到相关事件。'}</p> : <EventTimeline rows={visibleRows} isMock={memory.is_mock} label={label} actionName={actionName} references={references} confidenceOf={confidenceOf} entityType={entityType} selectedEventId={selectedEventId} onSelect={selectEvent}/>}
                {memory && rows.length > 20 && <div className="pagination"><span>第 {Math.min(eventPage,pageCount)} / {pageCount} 页</span><div><button disabled={eventPage <= 1} onClick={() => setEventPage(p => p-1)}>上一页</button><button disabled={eventPage >= pageCount} onClick={() => setEventPage(p => p+1)}>下一页</button></div></div>}
              </section>
              <SafetyPanel key={selected} videoId={selected} running={running} readOnly={currentVideo?.read_only_demo} onSeek={ms => { const event = visibleEvents.find(e => e.start_ms === ms); if (event) selectEvent(event); else void seekTo(ms); }}/>
            </section>
            <section className="panel answer-panel" id="inspector-panel-qa" role="tabpanel" aria-labelledby="inspector-tab-qa" hidden={inspectorTab !== 'qa'} tabIndex={0}>
              <div className="panel-head"><strong><Icon name="chat" size={18}/>AI 问答</strong></div><div className="answer-content">
                <p className="workspace-qa-context">{currentVideo ? `当前录像：${currentVideo.name}` : '导入视频后，分析结果会显示在这里。'}</p>
                {(!memory || running) && <p className="qa-prerequisite">请先完成视频分析，我再为你介绍录像中的情况。</p>}
                <form onSubmit={ask}><label htmlFor="question">你想了解什么情况？</label>{entityId && <p className="query-context">当前对象：{label(entityId)} <button type="button" className="text-button" onClick={() => setEntityId('')}>清除</button></p>}<div className="question-input"><input id="question" name="question" autoComplete="off" value={question} onChange={e => setQuestion(e.target.value)} placeholder="例如：视频中有没有人摔倒？" maxLength={500}/><button className="primary" type="submit" disabled={!memory || running || asking || !question.trim()}>{asking ? '正在回答…' : '提问'}</button></div></form>
                <div className="suggestions">{commonQuestions.map(text => <button key={text} disabled={asking} onClick={() => { setEntityId(''); setQuestion(text); }}>{text}</button>)}</div>
                {!answer && memory && !running ? <div className="answer-empty"><Icon name="chat" size={24}/><div><h2>想了解录像里的哪一刻？</h2><p>可以问人物活动、物品位置或安全提醒。</p></div></div> : answer ? <div className="answer-result" aria-live="polite">
                  {observations.map((c,i) => <div className="claim" key={`o${i}`}><p>{familyClaim(c,confidenceContext)}</p>{references(c.evidence_ids)}</div>)}
                  {inferences.map((c,i) => <div className="claim inference" key={`i${i}`}><p>{familyClaim(c,confidenceContext)}</p>{references(c.evidence_ids)}</div>)}
                  {answerUnknowns.map((text,i) => <p className="answer-uncertainty" key={`u${i}`}>{text}</p>)}
                  {answerCandidates.length > 0 && <div className="candidates">你想了解哪一个对象？{answerCandidates.map(c => <button className="reference" key={c.id} onClick={() => { setEntityId(c.id); changeInspector('tools'); setAnswer(null); }}>{familyEntityLabel(c.label)}</button>)}</div>}
                </div> : null}
                {!!conversations[selected]?.length && <details className="conversation-history" open><summary>对话历史 <span>{conversations[selected].length}</span></summary><ol>{conversations[selected].map(item => <li key={item.id}><button type="button" aria-pressed={answer === item.answer} onClick={() => { setQuestion(item.question); setAnswer(item.answer); }}><Icon name="chat" size={15}/><span>{item.question}</span><Icon name="arrow" size={14}/></button></li>)}</ol></details>}
              </div>
            </section>
            <section id="inspector-panel-tools" role="tabpanel" aria-labelledby="inspector-tab-tools" hidden={inspectorTab !== 'tools'} tabIndex={0}>
              <div className="inspector-section-heading"><h2>目标追踪</h2></div>
              {!memory ? <p className="empty">完成视频分析后，可在这里回顾人物或物品的踪迹。</p> : <div className="replay-view"><label htmlFor="entity-select">选择要回顾的对象</label><select id="entity-select" value={entityId} onChange={e => { setEntityId(e.target.value); setAnswer(null); }}><option value="">请选择一个对象</option>{replayEntities.map(e => <option key={e.id} value={e.id}>{label(e.id)}</option>)}</select>
                {!replay ? <p className="empty">选择人物或物品，查看它在录像中的活动。</p> : <><p className="replay-title">{label(replay.entity.id)}的录像片段</p>{!replayEntries.length && <p className="empty">暂时没有可回放的相关活动。</p>}{replayDisplayEntries.map(entry => {
                  const evidence = memory?.evidence.find(e => entry.evidence_ids.includes(e.id));
                  const frame = memory?.frames.find(f => evidence?.frame_ids.includes(f.id));
                  return <article className="replay-entry" data-source-count={entry.sourceEntries.length || 1} key={entry.id}>{frame && <button className="frame-link" onClick={() => evidence && seek(evidence)} aria-label={`回放 ${stamp(entry.start_ms)}`}><img src={frame.url} alt={`${label(entry.event.subject_id)} ${stamp(entry.start_ms)}`} loading="lazy"/><span><Icon name="play" size={13}/>{stamp(entry.start_ms)}</span></button>}<div><p>{replayDescription(entry,label,entityType,actionName)}</p>{entry.rule_id === 'conditional_containment_v1' && <p className="muted">这个位置取决于物品仍在容器中，暂时无法确认当前的位置。</p>}{references(entry.evidence_ids)}</div></article>;
                })}{replay.unknowns.length > 0 && <p className="answer-uncertainty">部分活动或位置暂时无法确认。</p>}<p className="muted">这些片段只反映录像中的相关时刻。</p></>}
              </div>}
            </section>
            {activeEvidence && <div className="evidence-view"><div className="section-heading"><h2>相关画面</h2></div><div className="frames">{activeEvidence.frame_ids.map(id => { const frame = memory?.frames.find(f => f.id === id); return frame ? <figure key={id}><img src={frame.url} alt={`原视频画面 ${stamp(frame.pts_ms)}`}/><figcaption>{stamp(frame.pts_ms)}</figcaption></figure> : null; })}</div></div>}
          </div>
        </aside>}
      </div>
      {!selected && <VideoRecognitionCapabilities/>}

      </>}
    </main>
    </>}
    {deleteTarget && <DeleteVideoDialog key={deleteTarget.id} name={deleteTarget.name} deleting={deleting} error={deleteError} onCancel={() => { if (!deleting) setDeleteTarget(null); }} onConfirm={() => void confirmDelete()}/>}
  </Shell>;
}

createRoot(document.getElementById('root')!).render(<StrictMode><AccountGate><App /></AccountGate></StrictMode>);
