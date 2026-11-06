import { StrictMode, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './design-tokens.css';
import './style.css';
import './dashboard.css';
import { Shell } from './Shell';
import { ReviewPanel } from './ReviewPanel';
import { LiveCameraPanel } from './LiveCameraPanel';
import { Icon, mergedRanges, sizeLabel, stamp, durationLabel } from './ui';
import { EventTimeline, timelineRows, type EventGroup } from './EventTimeline';
import { LandingPage, type DemoVideo } from './LandingPage';
import { DeleteVideoDialog } from './DeleteVideoDialog';
import { SafetyPanel } from './SafetyPanel';
import { FlameScanSetting, VideoAnalysisControls, SafetyScanProgress, SafetyAnalysisPlan, analysisPath, planPath, jobMode, modeLabel, type AnalysisJob, type StartMode, type AnalysisMode, type FlameScan } from './VideoAnalysis';
import { eventClass, replayDescription } from './eventPresentation';
import { HIGH_SCORE, MIN_SCORE, confidenceIndex, eventConfidence, confidenceLabel, displayClaim, isPriorityEvent, visibleText, type SourceClaim } from './confidence';
import './theme.css';
import './motion.css';
import './redesign.css';
import { introductionFromHash, type IntroductionPage } from './introduction';
import { viewFromUrl, inspectorFromUrl, type InspectorTab, type View } from './navigation';
import { InspectorTabs } from './VideoAnalysisWorkspace';
import { EvidenceTimebar } from './EvidenceTimebar';
import { seekVideo } from './seekVideo';
import './unified-workspace.css';
import './swiss-site.css';
import './swiss-workspace.css';

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
const intervalText = (seconds: number) => seconds >= 60 ? `${Number((seconds/60).toFixed(1))} 分钟` : `${seconds} 秒`;
const statusName: Record<string, string> = { queued: '等待分析', running: '分析中', paused: '已保存进度', completed: '分析完成', partial: '部分完成', failed: '分析失败', interrupted: '分析中断', answered: '有证据回答', unknown: '无法确定', ambiguous: '需要消歧' };
const actionName: Record<string, string> = { seen_at: '出现在', place_on: '被放在', pick_up: '被拿起', put_in: '被放入', take_out: '被取出于', move: '移动到', open: '被打开', close: '被关闭', explicit_exit: '离开视野', occluded: '被遮挡', unknown: '动作无法确定', walk:'行走', sit_down:'坐下', stand_up:'起身', lie_down:'躺下', lying_on_floor:'躺卧于地面', fall:'出现疑似跌倒过程', climb:'攀爬', help_gesture:'做出疑似求助动作', no_person_visible:'所引用采样画面未见人物',smoke:'出现烟雾迹象',flame:'出现明火迹象',sparks:'出现火花／电弧迹象' };
const videoFromUrl = () => { const id = new URLSearchParams(location.search).get('video') || ''; return /^v_[A-Za-z0-9_-]{1,80}$/.test(id) ? id : ''; };

function App() {
  const [view, setView] = useState<View>(() => viewFromUrl(new URL(location.href)));
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>(() => inspectorFromUrl(new URL(location.href)));
  const [toolsTab, setToolsTab] = useState<'objects' | 'review'>('objects');
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
  const analysisSettings = useRef<HTMLDetailsElement>(null);
  const [planRevision, setPlanRevision] = useState(0);
  const [planError, setPlanError] = useState('');
  const [answer, setAnswer] = useState<Answer | null>(null);
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
  const [eventKind, setEventKind] = useState('all');
  const [eventCategory, setEventCategory] = useState<'safety' | 'all' | 'object' | 'person' | 'environment'>('safety');
  const [personEventKind, setPersonEventKind] = useState<'general' | 'important'>('important');
  const [hour, setHour] = useState('all');
  const [eventPage, setEventPage] = useState(1);
  const [foldEvents, setFoldEvents] = useState(true);
  const [jumpTime, setJumpTime] = useState('00:00:00');
  const [playbackTime, setPlaybackTime] = useState(0);
  const [asking, setAsking] = useState(false);
  const [useTextModel, setUseTextModel] = useState(true);
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
    setMemory(null); setAnswer(null); setJob(null); setActiveEvidence(null); setSeekMessage(''); setError(''); setEntityId(''); setReplay(null); setPlan(null); setHour('all'); setEventSearch(''); setEventKind('all'); setEventPage(1); setPlaybackTime(0); setJumpTime('00:00:00');
    setFoldEvents(true);
    setEventCategory('safety'); setPersonEventKind('important'); setAnalysisMode('safety');
    setFlameScan('normal');
    if (!selected) return;
    let cancelled = false;
    api<Video & { latest_job: Job | null; metadata?: {case_type?: string} }>(`/api/videos/${selected}`).then(async detail => {
      if (cancelled) return;
      setJob(detail.latest_job);
      if (detail.latest_job?.analysis_mode === 'objects' || (!detail.latest_job?.analysis_mode && detail.metadata?.case_type === 'long_video')) { setAnalysisMode('objects'); setEventCategory('object'); }
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
    setAnalysisMode(mode); setEventCategory(mode === 'objects' ? 'object' : 'safety');
    setEntityId(''); setReplay(null); setAnswer(null); setEventKind('all'); setEventPage(1);
  };
  const chooseFlameScan = (scan: FlameScan) => {
    setFlameScan(scan);
    if (analysisMode !== 'safety') changeAnalysisMode('safety');
    if (analysisSettings.current) analysisSettings.current.open = true;
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
    try { const next = await api<Job>(`/api/videos/${id}/pause`, { method: 'POST' }); if (selectedRef.current === id) setJob(next); }
    catch (e) { if (selectedRef.current === id) setError((e as Error).message); }
    finally { analysisActionRef.current = false; setAnalysisAction(false); }
  };
  const ask = async (event: React.FormEvent) => {
    event.preventDefault(); if (!question.trim() || asking || !memory || running) return;
    const id = selected;
    setAsking(true); setError('');
    try {
      const result = await api<Answer>(`/api/videos/${id}/query`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: question.trim(), entity_id: entityId || undefined, use_model: useTextModel }) });
      if (selectedRef.current === id) setAnswer(result);
    } catch (e) { if (selectedRef.current === id) setError((e as Error).message); }
    finally { if (selectedRef.current === id) setAsking(false); }
  };
  useEffect(() => () => { seekOperation.current?.abort(); }, [selected, view]);
  useEffect(() => {
    const pane = document.querySelector('.workspace-inspector-scroll');
    if (pane) pane.scrollTop = 0;
  }, [inspectorTab, toolsTab]);
  const seekTo = async (timeMs: number) => {
    seekOperation.current?.abort();
    const controller = new AbortController(); seekOperation.current = controller;
    const player = videoRef.current; const id = selected;
    if (!player) { setSeekMessage('请先选择可播放的录像。'); return; }
    setSeekMessage('正在定位证据…');
    try {
      const actual = await seekVideo(player, timeMs, controller.signal);
      if (!controller.signal.aborted && selectedRef.current === id) {
        setPlaybackTime(actual);
        setSeekMessage(Math.abs(actual - timeMs / 1000) < .25 ? `已定位至 ${stamp(timeMs)}` : '定位存在偏差，请结合关键帧核对。');
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
    return evidence ? <button className="reference" key={id} onClick={() => seek(evidence)} aria-pressed={activeEvidence?.id === id} aria-label={`查看证据 ${stamp(evidence.start_ms)}`}>{stamp(evidence.start_ms)}–{stamp(evidence.end_ms)} <Icon name="arrow-up-right" size={13}/></button> : null;
  });
  const label = (id: string | null) => memory?.entities.find(e => e.id === id)?.label || '';
  const entityType = (id: string) => memory?.entities.find(e => e.id === id)?.entity_type;

  const confidenceContext = useMemo(() => confidenceIndex(memory), [memory]);
  const levels = useMemo(() => new Map((memory?.events || []).map(e => [e.id, eventConfidence(e,confidenceContext)])), [memory,confidenceContext]);
  const confidenceOf = (e: Event) => levels.get(e.id) ?? eventConfidence(e,confidenceContext);
  const visibleEvents = (memory?.events || []).filter(e => confidenceOf(e) !== 'hidden' && eventClass(e,confidenceContext.entities) !== 'support');
  const importantOnly = eventCategory === 'environment' || (eventCategory === 'person' && personEventKind === 'important');
  const scopedEvents = visibleEvents.filter(e => {
    const category = eventClass(e,confidenceContext.entities);
    if (eventCategory === 'all') return true;
    if (eventCategory === 'safety') return category === 'person_important' || category === 'environment';
    return category === (eventCategory === 'person' ? (personEventKind === 'important' ? 'person_important' : 'person_general') : eventCategory);
  });
  const visibleSubjectIds = new Set(visibleEvents.map(e => e.subject_id));
  const selectableEntities = (memory?.entities || []).filter(e => visibleSubjectIds.has(e.id));
  const answerCandidates = (answer?.candidates || []).filter(e => visibleSubjectIds.has(e.id));
  const eventCounts = { high: 0, medium: 0 };
  for (const e of scopedEvents) { const level=confidenceOf(e); if (level !== 'hidden') eventCounts[level]++; }
  const filteredEvents = scopedEvents.filter(e => (eventKind === 'all' || confidenceOf(e) === eventKind) && (hour === 'all' || (e.start_ms < (Number(hour)+1)*3600000 && e.end_ms >= Number(hour)*3600000)) && `${label(e.subject_id)} ${label(e.target_id)} ${actionName[e.action] || ''}`.toLocaleLowerCase().includes(eventSearch.toLocaleLowerCase()));
  const rows = timelineRows(filteredEvents, memory?.event_groups || [], foldEvents);
  const replayEntries = (replay?.entries || []).flatMap(entry => {
    const category = eventClass(entry.event,confidenceContext.entities);
    if (category === 'support' || (eventCategory === 'safety' && !['person_important', 'environment'].includes(category)) || (eventCategory === 'object' && category !== 'object')
        || (eventCategory === 'environment' && category !== 'environment')
        || (eventCategory === 'person' && category !== (personEventKind === 'important' ? 'person_important' : 'person_general'))) return [];
    const level = entry.rule_id === 'conditional_containment_v1'
      ? displayClaim({text:entry.condition || '',event_ids:entry.source_event_ids,evidence_ids:entry.evidence_ids,rule_id:entry.rule_id},confidenceContext,true)?.confidence || 'hidden'
      : confidenceOf(entry.event);
    return level === 'hidden' ? [] : [{...entry,confidence:level}];
  });
  const replayEntities = selectableEntities.filter(e => eventCategory === 'all' || scopedEvents.some(event => event.subject_id === e.id));
  const questionSuggestions = eventCategory === 'object'
    ? selectableEntities.filter(e => e.entity_type === 'object').slice(0, 3).map(e => `${e.label}最后在哪里出现？`)
    : eventCategory === 'environment' ? ['视频中有没有可见明火？','哪些明火候选经过了复核？']
    : eventCategory === 'person' ? ['视频中出现了几个人？','视频中什么时候有人跌倒？','人物在视频中做了什么？']
    : ['视频中出现了几个人？','概括这段录像里发生的事情','视频中有哪些值得关注的情况？'];
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
  const answerUnknowns = [...new Set((answer?.unknowns || []).map(visibleText))];
  if (answer && !observations.length && !inferences.length && !answerUnknowns.length) answerUnknowns.push('当前没有足够可信的证据可供回答。');
  const pageCount = Math.max(1, Math.ceil(rows.length / 20));
  const visibleRows = rows.slice((Math.min(eventPage, pageCount)-1)*20, Math.min(eventPage, pageCount)*20);
  const safetyOnlyJob = !!job && jobMode(job) === 'safety';
  const ranges = mergedRanges(job?.analyzed_ranges || []);
  const staticRanges = mergedRanges(job?.skipped_static_ranges || []);
  const coverage = currentVideo ? Math.min(100, ranges.reduce((n, r) => n+r.end_ms-r.start_ms, 0)/currentVideo.duration_ms*100) : 0;
  const jump = () => {
    const parts = jumpTime.split(':').map(Number); const seconds = parts[0]*3600 + (parts[1] || 0)*60 + (parts[2] || 0);
    if (!currentVideo || !Number.isFinite(seconds) || seconds*1000 >= currentVideo.duration_ms) { setError('跳转时间需要在当前录像范围内。'); return; }
    setActiveEvidence(null); setSelectedEventId(null); void seekTo(seconds * 1000);
  };

  const configurationNotice = health && !health.analysis_ready ? <><span>录像云分析尚未就绪：{health.configuration_error?.message} 不影响本地摄像头 GPU 识别、上传和播放。</span><button className="text-button" onClick={() => refresh().catch(e => setError(e.message))}>刷新配置状态</button></> : null;

  return <Shell videos={videos} selected={selected} onSelect={id => { setSelected(id); navigate('videos'); }} ready={!!health?.analysis_ready} loading={!health && !healthError} serviceError={!!healthError} localCamera={!health?.cloud_mode} model={health?.model || ''} view={view} introductionPage={introductionPage} onIntroductionNavigate={navigateIntroduction} onNavigate={navigate} onDelete={requestDelete} busyVideoId={running ? selected : deleting ? deleteTarget?.id : undefined}>
    {notice && <div className="global-notice" role="status"><span>{notice}</span><button className="text-button" onClick={() => setNotice('')}>收起</button></div>}
    {view === 'home' ? <LandingPage page={introductionPage} onPageChange={navigateIntroduction} onEnter={() => navigate('videos')} onCamera={() => navigate('camera')} demos={demos} onOpenDemo={id => { setSelected(id); navigate('videos'); }}/>: <>
    {(health?.is_mock || memory?.is_mock || job?.is_mock || answer?.is_mock) && <div className="mock-banner" role="alert">测试模拟模式：事件由测试适配器提供，未执行真实AI识别，不可用作产品效果演示。</div>}
    <main id="main-content" className={view === 'camera' ? 'camera-workspace' : `video-workspace unified-workspace${videos.length === 0 ? ' is-empty-library' : ''}`} aria-label={view === 'camera' ? '实时摄像头工作台' : 'AI 视频分析工作台'} tabIndex={-1}>
      {view === 'camera' ? <>
        <header className="camera-page-heading">
          <div className="camera-page-heading-copy">
            <p className="camera-page-eyebrow"><Icon name="shield" size={15} />本地实时看护</p>
            <h1>实时摄像头</h1>
            <p>连接摄像头，查看实时画面与异常。</p>
          </div>
          <div className="camera-capability-list" aria-label="本地识别能力">
            <span><Icon name="activity" size={16} />跌倒</span>
            <span><Icon name="help-gesture" size={16} />举手求助</span>
            <span><Icon name="flame" size={16} />可见明火</span>
          </div>
        </header>
        {health?.cloud_mode ? <div className="configuration"><span>当前在线环境不支持连接本机摄像头。请在本机启动服务后使用实时摄像头，或导入视频进行分析。</span><button className="text-button" onClick={() => navigate('videos')}>导入视频</button></div> : <LiveCameraPanel ready={health !== null} configurationError={healthError} onRetry={() => { void refresh().catch(e => setError(e.message)); }} />}
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
          <div className="panel-head"><div className="video-panel-heading"><strong>视频回放</strong><span className="workspace-task-state" role="status" aria-label="当前分析任务状态">{job ? statusName[job.status] || job.status : selected ? '尚未分析' : '等待导入录像'}</span></div><div className="video-panel-actions">{selected && <button type="button" className="recording-import" disabled={busy} onClick={() => recordingInput.current?.click()}><Icon name="upload" size={16}/>{busy ? (uploadProgress < 100 ? `正在上传 ${uploadProgress}%` : '正在准备…') : '导入新录像'}</button>}</div></div>
      {busy && <div className="upload-progress" aria-live="polite"><div><strong>{uploadName}</strong><span>{uploadProgress < 100 ? `已上传 ${uploadProgress}%` : '文件传输完成，正在校验并准备播放。长录像可能需要一些时间。'}</span>{uploadProgress < 100 && <button className="text-button" onClick={() => { uploadController.current?.abort(); uploadRequest.current?.abort(); }}>取消上传</button>}</div><progress aria-label="上传进度" max={100} value={uploadProgress} /></div>}
          {videos.length > 0 && <div className="video-select"><label htmlFor="video-select">当前视频</label><select id="video-select" value={selected} onChange={e => setSelected(e.target.value)}><option value="">选择已上传视频</option>{videos.map(v => <option value={v.id} key={v.id}>{v.name} · {stamp(v.duration_ms)}</option>)}</select>{currentVideo && <button type="button" className="delete-video-button" disabled={running || deleting || busy || currentVideo.read_only_demo} onClick={() => requestDelete(currentVideo)}><Icon name="trash" size={14}/>删除视频</button>}</div>}
          {currentVideo && !currentVideo.read_only_demo && <FlameScanSetting value={flameScan} onChange={chooseFlameScan} disabled={running || analysisAction} />}
          {selected ? <video key={`video-${selected}`} ref={videoRef} controls playsInline preload="metadata" onLoadedMetadata={e => setMediaDuration(e.currentTarget.duration)} onTimeUpdate={e => setPlaybackTime(e.currentTarget.currentTime)} src={`/media/videos/${selected}`} onError={() => setError('视频播放器加载失败，请检查本地媒体服务或重新上传。')}  /> : <button type="button" className={`empty-video upload-dropzone ${dragging ? 'is-dragging' : ''}`} disabled={busy} aria-label="导入居家录像" aria-busy={busy} onClick={() => recordingInput.current?.click()} onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = busy ? 'none' : 'copy'; if (!busy) setDragging(true); }} onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false); }} onDrop={e => { e.preventDefault(); setDragging(false); void upload(e.dataTransfer.files[0]); }}><span className="empty-upload-icon"><Icon name="upload" size={26}/></span><strong>从一段居家录像开始</strong><span className="empty-upload-action">{busy ? (uploadProgress < 100 ? `正在上传 ${uploadProgress}%` : '正在准备录像…') : '导入居家录像'}</span><small>{dragging ? '松开即可导入录像' : `点击选择或拖入 MP4 · ${health ? health.limits.max_video_seconds === null ? '不设大小与时长上限' : `最长${durationLabel(health.limits.max_video_seconds*1000)}` : '正在读取上传设置…'}`}</small></button>}
          {currentVideo && <div className="recording-controls"><div className="playback-clock"><Icon name="clock" size={15} /><span>{stamp(playbackTime*1000)}</span><span>/ {stamp(currentVideo.duration_ms)}</span></div><div className="time-jump"><input aria-label="跳转录像时间" type="time" step="1" value={jumpTime} onChange={e => setJumpTime(e.target.value)} /><button onClick={jump}>跳转</button></div></div>}

          {currentVideo && <EvidenceTimebar durationSec={mediaDuration || currentVideo.duration_ms / 1000} timeSec={playbackTime} ranges={filteredEvents.flatMap(event => event.evidence_ids.flatMap(id => { const evidence = memory?.evidence.find(item => item.id === id); return evidence ? [{ id: `${event.id}:${id}`, startMs: evidence.start_ms, endMs: evidence.end_ms, label: visibleText(actionName[event.action] || event.action), selected: selectedEventId === event.id }] : []; }))} coverage={ranges} onSeek={time => { setActiveEvidence(null); setSelectedEventId(null); void seekTo(time * 1000); }} onSelect={id => { const event = filteredEvents.find(item => id.startsWith(`${item.id}:`)); if (event) { changeInspector('events'); selectEvent(event); } }} label="工作台视频时间定位"/>}
          <p className="workspace-seek-status" role="status">{seekMessage}</p>
          {selected && <details className="workspace-task-tray" ref={analysisSettings}><summary><span>分析任务与设置</span><span>{job ? `${modeLabel(jobMode(job))} · ${statusName[job.status] || job.status}` : '选择模式并开始分析'}</span></summary>
          {selected && <VideoAnalysisControls mode={analysisMode} onModeChange={changeAnalysisMode} job={job} pending={analysisAction} ready={!!health?.analysis_ready} demo={!!currentVideo?.read_only_demo} onStart={() => void analyze(analysisMode)} onResume={() => void analyze(jobMode(job), true)} onPause={() => void pauseAnalysis()} />}
          {job && jobMode(job) !== 'objects' && <SafetyScanProgress safety={job.safety} />}
          {currentVideo && job && !safetyOnlyJob && <div className="coverage"><div><span>{modeLabel(jobMode(job))}覆盖</span><strong>{job.status === 'completed' ? (staticRanges.length ? '处理完成 · 含静止过滤' : '全片已处理 · 采样分析') : `${job.completed_windows || 0} / ${job.total_windows || (analysisMode === 'objects' ? plan?.total_windows : undefined) || '…'} 个窗口`}</strong></div><div className="coverage-track" aria-label="已分析时间范围">{staticRanges.map(r => <span className="static-screened" key={`static-${r.start_ms}`} title={`静止筛查，未调用模型 ${stamp(r.start_ms)}–${stamp(r.end_ms)}`} style={{ left: `${r.start_ms/currentVideo.duration_ms*100}%`, width: `${(r.end_ms-r.start_ms)/currentVideo.duration_ms*100}%` }} />)}{job.status === 'completed' && !staticRanges.length ? <span style={{ left: 0, width: '100%' }} /> : ranges.map(r => <span key={r.start_ms} title={`${stamp(r.start_ms)}–${stamp(r.end_ms)}`} style={{ left: `${r.start_ms/currentVideo.duration_ms*100}%`, width: `${(r.end_ms-r.start_ms)/currentVideo.duration_ms*100}%` }} />)}</div><div className="coverage-labels"><span>00:00</span><span>{staticRanges.length ? '绿色：模型分析 · 灰色：静止过滤' : job.status === 'completed' ? '绿色为已处理范围' : ranges.length ? `${coverage.toFixed(1)}% 时段已处理，其他时段待分析` : '尚无可展示的已分析范围'} </span><span>{stamp(currentVideo.duration_ms)}</span></div></div>}
          {!safetyOnlyJob && job?.activity_filter && <details className="activity-summary"><summary>静止过滤：已跳过 {job.activity_filter.skipped_windows} 个窗口 · 模型分析 {job.activity_filter.model_analyzed_windows} 个窗口</summary><p>灰色时段仅做本地画面变化筛查，未调用视觉模型，不表示无人、物品未移动或没有危险。{job.activity_filter.risk_followup && '已出现风险候选，后续保持模型分析。'}</p>{staticRanges.length > 0 && <ul>{staticRanges.slice(0,20).map(r => <li key={r.start_ms}>{stamp(r.start_ms)}–{stamp(r.end_ms)} · 静止过滤</li>)}</ul>}{staticRanges.length > 20 && <p>另有 {staticRanges.length-20} 段，完整范围已保存。</p>}</details>}
          {planMode !== 'safety' && plan?.analysis_mode === planMode && plan.activity_filter_enabled && <p className="activity-plan">{job?.activity_filter || running ? '本次分析已启用静止过滤' : '下次分析将启用静止过滤'} · 变化片段优先分析 · 静止时每约 {intervalText(plan.activity_filter_review_seconds || 300)} 抽查；发现人物时每约 {intervalText(plan.activity_filter_person_review_seconds || 60)} 抽查。</p>}
          {selected && planMode === 'safety' && <SafetyAnalysisPlan plan={plan?.analysis_mode === 'safety' ? plan : null} />}
          {selected && planError && <p className="warning" role="status">{modeLabel(planMode)}计划暂不可用：{planError}</p>}
          {planMode !== 'safety' && plan?.analysis_mode === planMode && <details className="analysis-details"><summary>{modeLabel(planMode)}计划与预算 <span>{plan.total_windows} 个窗口 · 批次自动接续</span></summary><p className="analysis-plan">全片共 {plan.total_windows} 个窗口（{plan.activity_filter_enabled ? '过滤前调用上界，实际取决于画面变化与抽查' : '未计重试'}） · {plan.sampling_fps}帧/秒 · 每窗{plan.window_seconds}秒。每批预计最多 {plan.max_windows_per_batch} 个窗口，完成后自动接续；重试会占用额度。今日剩余 {plan.daily_requests_remaining} 次请求，达到每日上限时保存并暂停。可随时点击暂停分析，当前处理结束后生效。实际费用待账单确认。</p></details>}
          {job?.status === 'paused' && <p className="warning">{job.error?.message} {modeLabel(jobMode(job))}进度已保存。{!safetyOnlyJob && `已处理 ${job.completed_windows ?? '待上报'} / ${job.total_windows ?? '待上报'} 个窗口。`}后续尚未处理，当前问答只使用已有证据。</p>}
          {job?.status === 'partial' && safetyOnlyJob && <p className="warning">安全分析仅部分完成。{job.error?.message} 请查看扫描与复核统计；未处理、未复核或不可用的片段无法据此排除风险。{job.resumable && '可点击继续上次安全分析。'}</p>}
          {job?.status === 'partial' && !safetyOnlyJob && <p className="warning">有{job.failed_windows.length}个片段识别失败，时间线与答案不完整。识别缺口：{job.failed_windows.slice(0,5).map((w,i) => w.start_ms != null && w.end_ms != null ? `${stamp(w.start_ms)}–${stamp(w.end_ms)}` : `片段${i+1}`).join('、')}{job.failed_windows.length > 5 ? '等' : ''}。{job.resumable && `可点击继续上次${modeLabel(jobMode(job))}补齐；`}</p>}

          </details>}
        </section>
        </div>
        <aside className="analysis-inspector" aria-label="当前视频上下文工具">
          <InspectorTabs active={inspectorTab} onChange={changeInspector}/>
          <div className="workspace-inspector-scroll">
            <section id="inspector-panel-events" role="tabpanel" aria-labelledby="inspector-tab-events" hidden={inspectorTab !== 'events'} tabIndex={0}>
              <div className="inspector-section-heading"><h2>事件与证据</h2><span>{scopedEvents.length} 条线索</span></div>
<nav className="event-categories" aria-label="事件分类">{([['safety','安全概览'],['person','人物事件'],['environment','环境危险事件'],['object','物体事件'],['all','全部事件']] as const).map(([key,text]) => <button key={key} aria-pressed={eventCategory === key} onClick={() => { setEventCategory(key); setEntityId(''); setReplay(null); setAnswer(null); setEventKind('all'); setEventPage(1); }}>{text}</button>)}{eventCategory === 'person' && <div className="person-event-kinds" role="group" aria-label="人物事件分类">{([['general','一般事件'],['important','重要事件']] as const).map(([key,text]) => <button key={key} aria-pressed={personEventKind === key} onClick={() => { setPersonEventKind(key); setEventKind('all'); setEventPage(1); }}>{text}</button>)}</div>}</nav>
              <section className="timeline timeline-panel" id="timeline" aria-label="事件时间线">
<div className="confidence-legend"><span><i className="high-dot"/>绿色 · 较高可信</span><span><i className="medium-dot"/>黄色 · 中等可信</span><details><summary>颜色说明</summary><p>模型分数≥{HIGH_SCORE}且满足证据条件时为绿色；{MIN_SCORE}到不足{HIGH_SCORE}为黄色。缺少分数时，直接观察若有至少两个相隔1秒的有效原片时刻，且实体无歧义、没有不确定理由，也可按证据分为绿色，不会补造模型分数。推测、有歧义或带不确定理由的线索最多为黄色。低于{MIN_SCORE}、证据缺失或无法确定的事件不展示。</p></details></div>
            {memory && <div className="event-tools"><label className="event-search"><Icon name="search" size={16} /><input aria-label="搜索事件物品或动作" value={eventSearch} onChange={e => { setEventSearch(e.target.value); setEventPage(1); }} placeholder="搜索人物、物体、位置或动作" /></label><select aria-label="按录像小时筛选" value={hour} onChange={e => { setHour(e.target.value); setEventPage(1); }}><option value="all">全部时段</option>{Array.from({ length: Math.ceil((currentVideo?.duration_ms || 0)/3600000) }, (_, i) => <option key={i} value={i}>{String(i).padStart(2, '0')}:00–{String(i+1).padStart(2, '0')}:00</option>)}</select><div className="event-filters">{[['all', '全部', scopedEvents.length], ['high', '较高可信', eventCounts.high], ['medium', '中等可信', eventCounts.medium]].map(([key, text, count]) => <button key={key} className={eventKind === key ? 'active' : ''} onClick={() => { setEventKind(String(key)); setEventPage(1); }}>{text}<span>{count}</span></button>)}</div></div>}
            {memory && <div className="event-fold-controls"><span>当前显示 {rows.length} 项 · 对应 {filteredEvents.length} 条可见采样记录</span><button type="button" onClick={() => { setFoldEvents(value => !value); setEventPage(1); }}>{foldEvents ? '展开全部可见事件' : '折叠重复记录'}</button></div>}
              {!memory ? <p className="empty">{selected ? running ? '任务分析中，生成结果后显示事件。' : '当前录像尚无分析记录。' : '导入视频后，可以在这里查看事件。'}</p> : visibleEvents.length === 0 ? <p className="empty">当前没有达到展示条件的事件。证据不足或低可信线索已过滤。</p> : filteredEvents.length === 0 ? <p className="empty">当前筛选没有匹配事件，试试其他类别或时段。</p> : <EventTimeline rows={visibleRows} isMock={memory.is_mock} label={label} actionName={actionName} references={references} confidenceOf={confidenceOf} entityType={entityType} selectedEventId={selectedEventId} onSelect={selectEvent}/>}
              {memory && rows.length > 20 && <div className="pagination"><span>第 {Math.min(eventPage, pageCount)} / {pageCount} 页</span><div><button disabled={eventPage <= 1} onClick={() => setEventPage(p => p-1)}>上一页</button><button disabled={eventPage >= pageCount} onClick={() => setEventPage(p => p+1)}>下一页</button></div></div>}
              </section>
              {selected && <details className="workspace-alerts"><summary>预警与通知设置</summary><SafetyPanel key={selected} videoId={selected} running={running} readOnly={currentVideo?.read_only_demo} visible={eventCategory === 'safety' || eventCategory === 'all' || importantOnly} mode={eventCategory === 'environment' ? 'environment' : eventCategory === 'person' ? 'person' : 'all'} onSeek={ms => { const evidence = memory?.evidence.find(item => item.start_ms === ms); if (evidence) seek(evidence); else void seekTo(ms); }}/></details>}
            </section>
        <section className="panel answer-panel" id="inspector-panel-qa" role="tabpanel" aria-labelledby="inspector-tab-qa" hidden={inspectorTab !== 'qa'} tabIndex={0}><div className="panel-head"><strong><Icon name="chat" size={18} />询问录像中的情况</strong><span className="tag">证据问答</span></div><div className="answer-content">
          <p className="workspace-qa-context">{currentVideo ? `当前录像：${currentVideo.name}` : '导入视频后，可以在这里查看事件或提问。'}</p>{!memory && selected && <p className="muted">{running ? '分析任务进行中，生成记录后可提问。' : '当前录像尚无可用分析记录，请先完成分析。'}</p>}<form onSubmit={ask}><label htmlFor="question">你想了解什么情况？</label>{entityId && <p className="query-context">当前查询对象：{label(entityId)} <button type="button" className="text-button" onClick={() => setEntityId('')}>清除</button></p>}<div className="question-input"><input id="question" name="question" autoComplete="off" value={question} onChange={e => setQuestion(e.target.value)} placeholder={eventCategory === 'object' ? '例如：钥匙最后在哪里出现？' : '例如：视频中什么时候有人跌倒？'} maxLength={500} /><button className="primary" type="submit" disabled={!memory || running || asking || !question.trim()}>{asking ? '查询中…' : '提问'}</button></div></form>
          {memory && <div className="suggestions">{questionSuggestions.map(text => <button key={text} onClick={() => { setEntityId(''); setQuestion(text); }}>{text}</button>)}</div>}
          <div className="record-qa-settings"><label className="record-qa-option"><input type="checkbox" checked={useTextModel} onChange={e => setUseTextModel(e.target.checked)} disabled={asking}/>使用大模型解读已有记录</label><details className="record-qa-details"><summary>调用说明</summary><p className="muted record-qa-note">不重新分析视频。每次问答最多调用1次模型，计入每日上限；人数统计、重点事件检索、重复问题缓存和关闭此选项后的检索不新增调用。</p></details></div>
          {!answer ? <div className="answer-empty"><Icon name="chat" size={24}/><div><h2>每个提醒，都回到画面里。</h2><p>询问人物异常或环境危险，查看关联的视频证据。</p></div></div> : <div className="answer-result" aria-live="polite"><div className="section-heading"><h2>{answer.answer_style === 'attention' ? '值得关注的情况' : answer.answer_style === 'summary' ? '录像概括' : '视频中的线索'}</h2><span className="tag answer-state">{!observations.length && !inferences.length ? '证据不足' : '证据线索'}</span></div>
            {answer.answer_engine && <p className="muted">{answer.answer_engine === 'record_statistics' ? '基于已有记录的人数统计' : answer.answer_engine === 'record_llm' ? `大模型根据分析记录回答${answer.cached ? ' · 已保存回答，本次未调用模型' : ''}` : '本地记录检索'}</p>}
            {observations.map((c, i) => <div className={`claim confidence-${c.confidence}`} data-confidence={c.confidence} key={`o${i}`}><h3>{confidenceLabel(c.confidence)} · {c.record_summary ? '记录解读' : '直接观察'}</h3><p>{c.text}</p>{references(c.evidence_ids)}</div>)}
            {inferences.map((c, i) => <div className={`claim inference confidence-${c.confidence}`} data-confidence={c.confidence} key={`i${i}`}><h3>{confidenceLabel(c.confidence)} · 推测{c.time_range && <span className="claim-time">{c.time_range}</span>}</h3><p>{c.text}</p>{c.explanations && c.explanations.length > 0 && <details className="claim-explanations"><summary>推测依据与限制</summary>{c.explanations.map((reason,j) => <p className="muted" key={j}>{reason}</p>)}</details>}{references(c.evidence_ids)}</div>)}
            {answerUnknowns.map((text, i) => <div className="claim unknown" key={`u${i}`}><h3>未知边界</h3><p>{text}</p></div>)}
            {answerCandidates.length > 0 && <div className="candidates">选择要核对的对象：{answerCandidates.map(c => <button className="reference" key={c.id} onClick={() => { setEntityId(c.id); setToolsTab('objects'); changeInspector('tools'); setAnswer(null); }}>{c.label}（{c.appearance || '外观未明确'}）</button>)}</div>}
            <p className="warning">{answer.coverage_note}</p></div>}

        </div></section>
            <section id="inspector-panel-tools" role="tabpanel" aria-labelledby="inspector-tab-tools" hidden={inspectorTab !== 'tools'} tabIndex={0}>
              <div className="inspector-section-heading"><h2>追踪与人工复核</h2></div>
              <div className="workspace-tool-tabs" role="group" aria-label="工具类型"><button aria-pressed={toolsTab === 'objects'} onClick={() => setToolsTab('objects')}>物品追踪</button><button aria-pressed={toolsTab === 'review'} onClick={() => setToolsTab('review')}>人工复核</button></div>
              <div hidden={toolsTab !== 'objects'}>{!memory ? <p className="empty">完成当前录像分析后，可按对象查询并回顾踪迹。</p> : <><div className="replay-view"><label htmlFor="entity-select">选择要回顾的对象</label><select id="entity-select" value={entityId} onChange={e => { setEntityId(e.target.value); setAnswer(null); }}><option value="">请选择一个对象</option>{replayEntities.map(e => <option key={e.id} value={e.id}>{e.label}</option>)}</select>
              {!replay ? <p className="empty">选择对象，串起它在这段视频中的经历。</p> : <><p className="replay-title">{replay.entity.label}的记忆线索</p>{replayEntries.length === 0 && <p className="empty">没有可回放的直接事件。</p>}{replayDisplayEntries.map(entry => {
                const evidence = memory?.evidence.find(e => entry.evidence_ids.includes(e.id));
                const frame = memory?.frames.find(f => evidence?.frame_ids.includes(f.id));
                return <article className={`replay-entry confidence-${entry.confidence}${isPriorityEvent(entry.event) ? ' priority-event' : ''}`} data-confidence={entry.confidence} data-source-count={entry.sourceEntries.length || 1} key={entry.id}>{frame && <button className="frame-link" onClick={() => evidence && seek(evidence)} aria-label={`回放 ${stamp(entry.start_ms)}`}><img src={frame.url} alt={`${label(entry.event.subject_id)} ${stamp(entry.start_ms)}`} loading="lazy" /><span><Icon name="play" size={13}/> {stamp(entry.start_ms)}</span></button>}<div><span className={`tag ${entry.confidence === 'medium' ? 'amber' : ''}`}>{isPriorityEvent(entry.event) ? '重点关注 · ' : ''}{confidenceLabel(entry.confidence)}{entry.epistemic_type === 'inferred' ? ' · 推测' : ''}</span><p>{visibleText(replayDescription(entry,label,entityType,actionName))}</p>{entry.sourceEntries.length > 1 && <><small>{stamp(entry.start_ms)}–{stamp(entry.end_ms)} · 合并 {entry.sourceEntries.length} 条相关原始记录{entry.groupKind === 'repeated_observations' && ' · 多次采样看见，不表示期间始终在场'}</small><details className="event-group-sources"><summary>展开 {entry.sourceEntries.length} 条原始记录</summary>{entry.sourceEntries.map(source => <div key={source.id}><p>{stamp(source.start_ms)}–{stamp(source.end_ms)} {visibleText(replayDescription(source,label,entityType,actionName))}</p>{source.event.uncertainty_reasons.map((reason,i) => <p className="muted" key={i}>{visibleText(reason)}</p>)}{references(source.evidence_ids)}</div>)}</details></>}{entry.epistemic_type === 'inferred' && entry.rule_id !== 'conditional_containment_v1' && entry.event.uncertainty_reasons.length > 0 && <details><summary>推测依据与限制</summary>{entry.event.uncertainty_reasons.map((reason,i) => <p className="muted" key={i}>{visibleText(reason)}</p>)}</details>}{references(entry.evidence_ids)}</div></article>;
              })}{replay.unknowns.map((text, i) => <p className="warning" key={i}>{text}</p>)}<p className="muted">回放仅覆盖这段视频。黄色线索包含不确定性，请结合原片理解。</p></>}
            </div></>}</div>
              <div hidden={toolsTab !== 'review'}>{!selected ? <p className="empty">请选择录像后进行人工核对与画面标定。</p> : currentVideo?.read_only_demo ? <p className="empty">此案例只读。重新分析创建副本后，可保存人工复核。</p> : <>        {selected && !currentVideo?.read_only_demo && <section className="av-review-section" id="review" aria-label="人工复核与画面标定"><ReviewPanel key={selected} initialOpen={true} videoId={selected} memory={memory} videoRef={videoRef} running={running} onChange={() => loadMemory(selected)} onSeek={seek}/></section>}</>}</div>
            </section>
            <div hidden={inspectorTab === 'tools'}>          {activeEvidence && <div className="evidence-view"><div className="section-heading"><h2>证据关键帧</h2><span className="muted" role="status">{seekMessage}</span></div><div className="frames">{activeEvidence.frame_ids.map(id => { const frame = memory?.frames.find(f => f.id === id); return frame ? <figure key={id}><img src={frame.url} alt={`原视频关键帧 ${stamp(frame.pts_ms)}`} /><figcaption>{stamp(frame.pts_ms)}</figcaption></figure> : null; })}</div></div>}</div>
          </div>
        </aside>
      </div>

      </>}
    </main>
    </>}
    {deleteTarget && <DeleteVideoDialog key={deleteTarget.id} name={deleteTarget.name} deleting={deleting} error={deleteError} onCancel={() => { if (!deleting) setDeleteTarget(null); }} onConfirm={() => void confirmDelete()}/>}
  </Shell>;
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
