import { useEffect, useRef, useState } from 'react';
import './live-camera.css';

type ReviewStatus = 'pending' | 'confirmed' | 'rejected' | 'uncertain' | 'unavailable';
type LiveEvent = { candidate_id?: string; event_type: string; occurred_at: string; confidence: number; event_id?: string | null; review_status?: ReviewStatus; review_reason?: string; review_model?: string };
const reviewStateName: Record<ReviewStatus, string> = {
  pending: '待复核',
  confirmed: '已复核确认',
  rejected: '复核排除',
  uncertain: '证据不足',
  unavailable: '复核不可用',
};
function reviewLabel(reviewStatus: LiveEvent['review_status']) {
  return reviewStatus && Object.hasOwn(reviewStateName, reviewStatus)
    ? reviewStateName[reviewStatus]
    : '未复核';
}
type CameraStatus = { safety_pipeline?: string; review_model?: string; review_enabled?: boolean; review_error?: string; cloud_calls?: boolean; sparks_enabled?: boolean; recording: boolean; recording_status?: { enabled: boolean; state: string; directory?: string; segment_seconds?: number; retention_days?: number; max_gib?: number; min_free_gib?: number; files?: number; bytes?: number; deleted_files?: number; error?: string }; help_facing_camera_required?: boolean; help_face_candidates?: number | null; fall_diagnostics?: { track_id: string; state: string }[]; alerts?: { enabled: boolean; pending?: number; worker_alive?: boolean; error?: string }; state: string; frames: number; persons?: number; fire_candidates?: number; width?: number; height?: number; preview_width?: number; preview_height?: number; flame_tiling_enabled?: boolean; fps?: number; fire_fps?: number; fire_inference_ms?: number; fire_state?: string; inference_ms?: number; device?: string; frame_age_seconds?: number | null; events: LiveEvent[] };
const stateName: Record<string,string> = { stopped:'未开启', starting:'模型加载中', connecting:'连接摄像头', running:'本地实时识别中', reconnecting:'断线重连中', stale:'画面已过期', error:'连接异常' };
const recordingStateName: Record<string,string> = { disabled:'未启用', starting:'正在启动录像', recording:'正在录像', reconnecting:'断线重连中', paused_low_space:'磁盘空间不足，录像已暂停', error:'录像异常', stopped:'录像已停止' };
const eventName: Record<string,string> = { help_requested:'双手举高 · 疑似求助', suspected_fall:'疑似跌倒', fire_detected:'疑似明火' };
const fallStateName: Record<string,string> = { keypoints_unreliable:'肩膀或髋部关键点不清晰，请调整视角', observing_standing:'正在观察站立姿态', ready:'已观察到站立，持续监测中', need_standing:'尚未观察到连续站立，暂不能确认跌倒过程', observing_descent:'正在观察下降过程', confirming_posture:'已观察到下降，等待倒地姿态', confirming_hold:'正在确认倒地持续时间', cooldown:'处于报警冷却期', alert:'已产生疑似跌倒报警' };
type Props = { ready?: boolean; configurationError?: string; onRetry?: () => void };
export function LiveCameraPanel({ ready = true, configurationError = '', onRetry }: Props) {
  const [status,setStatus] = useState<CameraStatus | null>(null);
  const [error,setError] = useState('');
  const [busy,setBusy] = useState(false);
  const [image,setImage] = useState('');
  const [pollError,setPollError] = useState('');
  const [frameError,setFrameError] = useState('');
  const objectUrl = useRef('');
  const displayedFrame = useRef(-1);
  useEffect(() => {
    if (!ready) return;
    let active = true;
    let fetching = false;
    const controller = new AbortController();
    function clearFrame() {
      displayedFrame.current = -1;
      setImage('');
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
      objectUrl.current = '';
    }
    async function refresh() {
      if (fetching) return;
      fetching = true;
      try {
        const response = await fetch('/api/camera/status',{cache:'no-store',signal:controller.signal});
        if (!response.ok) throw new Error(`摄像头状态读取失败（HTTP ${response.status}），请确认本机服务可用。`);
        const next: CameraStatus = await response.json();
        if (!active) return;
        setStatus(next);
        setPollError('');
        if (next.state === 'running' && next.frames !== displayedFrame.current) {
          try {
            const frame = await fetch('/api/camera/frame.jpg',{cache:'no-store',signal:controller.signal});
            if (!frame.ok) throw new Error(`预览画面暂不可用（HTTP ${frame.status}），正在重试。`);
            const blob = await frame.blob();
            if (!active) return;
            const url = URL.createObjectURL(blob);
            const old = objectUrl.current;
            objectUrl.current = url; setImage(url); displayedFrame.current = next.frames;
            setFrameError('');
            if (old) URL.revokeObjectURL(old);
          } catch (e) {
            if (active && !controller.signal.aborted) { clearFrame(); setFrameError((e as Error).message); }
          }
        } else if (next.state !== 'running') {
          clearFrame();
          setFrameError('');
        }
      } catch (e) { if (active && !controller.signal.aborted) { setPollError((e as Error).message); clearFrame(); } }
      finally { fetching = false; }
    }
    void refresh();
    const timer = window.setInterval(() => { void refresh(); },66);
    return () => { displayedFrame.current=-1; active=false; controller.abort(); window.clearInterval(timer); if (objectUrl.current) URL.revokeObjectURL(objectUrl.current); };
  },[ready]);
  async function command(action: 'start' | 'stop') {
    setBusy(true); setError('');
    try {
      const response = await fetch(`/api/camera/${action}`,{method:'POST'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error?.message || '摄像头操作失败');
      setStatus(data);
      if (action === 'stop') setImage('');
    } catch (e) {setError((e as Error).message);}
    finally {setBusy(false);}
  }
  const loading = !ready || (!status && !pollError);
  const loadingLabel = configurationError ? '运行环境读取失败' : !ready ? '正在确认本机运行环境' : '正在读取摄像头状态';
  const running = !!status && ['starting','connecting','running','reconnecting','stale'].includes(status.state);
  const state = configurationError ? 'error' : loading ? 'loading' : pollError ? 'unavailable' : status?.state || 'unavailable';
  const stateLabel = configurationError ? '运行环境读取失败' : pollError ? '状态更新中断' : loading ? loadingLabel : stateName[state] || '未知状态';
  const current = ready && !configurationError && !pollError && status?.state === 'running';
  const previewTitle = configurationError ? '运行环境读取失败' : pollError ? '暂时无法更新摄像头状态' : loading ? loadingLabel : frameError ? '暂时无法获取预览画面' : state === 'stopped' ? '等待开启本地摄像头' : state === 'running' ? '正在获取最新画面…' : stateName[state] || '摄像头状态待确认';
  const previewHint = configurationError || pollError ? '请检查本机服务；连接恢复后会自动更新。' : loading ? '正在读取摄像头状态…' : frameError ? '正在重试获取画面，检测状态见右侧。' : state === 'stopped' ? '点击“开启摄像头”，查看实时画面。' : state === 'error' ? '请检查摄像头配置后重新开启。' : state === 'stale' || state === 'reconnecting' ? '恢复连接后，最新画面会出现在这里。' : '请稍候，画面会在连接就绪后显示。';
  const recordingStatus = status?.recording_status;
  return <section id="camera" className="live-camera-panel" aria-label="实时摄像头" aria-busy={loading && !configurationError}>
    <div className="live-camera-heading">
      <div><h2>实时摄像头</h2><p>摔倒 · 举手求助 · 可见明火</p></div>
      <div className="live-camera-controls"><span className="live-camera-state" data-state={state} role="status"><i aria-hidden="true"/>{stateLabel}</span><button disabled={busy || !status || !ready || !!configurationError} aria-busy={busy} onClick={() => void command(running ? 'stop' : 'start')}>{busy ? '处理中…' : running ? '停止预览' : '开启摄像头'}</button></div>
    </div>
    {configurationError && <p className="error" role="alert">{configurationError}{onRetry && <button className="text-button" onClick={onRetry}>重新读取</button>}</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {pollError && <p className="error" role="alert">{pollError}</p>}
    {!pollError && frameError && <p className="error" role="alert">{frameError}</p>}
    <div className="live-camera-viewport"><div className="live-camera-grid">
      <div className="live-camera-main">
        <div className="live-camera-screen"><span className="live-camera-screen-label">本机摄像头 · 16:9</span>{image && current && !frameError ? <img src={image} alt="摄像头实时画面，含本地人体框与姿态关键点" width={status?.preview_width || 1280} height={status?.preview_height || 720}/> : <div className="live-camera-placeholder"><svg className="camera-placeholder-icon" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true"><rect x="3" y="5" width="13" height="14" rx="3"/><path d="m16 10 5-3v10l-5-3"/></svg><strong>{previewTitle}</strong><span>{previewHint}</span></div>}</div>
        <div className="live-camera-session"><h3>本次会话事件</h3>{!status?.events?.length ? <p className="muted">暂无候选事件</p> : <ul className="live-camera-events" aria-label="本次会话事件列表" tabIndex={0}>{status.events.map((event,i) => <li key={event.candidate_id || event.event_id || `${event.occurred_at}-${i}`}>
          <div className="live-camera-event-content">
            <strong>{eventName[event.event_type] || event.event_type}</strong>
            <span className="live-camera-event-review" data-review={event.review_status}>{reviewLabel(event.review_status)}</span>
            {event.review_reason && <p>复核说明：{event.review_reason}</p>}
            {event.review_model && <p>复核模型：{event.review_model}</p>}
          </div>
          <time dateTime={event.occurred_at}>{new Date(event.occurred_at).toLocaleTimeString()}</time>
        </li>)}</ul>}</div>
      </div>
      <div className="live-camera-detail" aria-label="实时状态滚动区域" tabIndex={0}>
        <h3>实时状态</h3>
        <dl className="live-camera-metrics">
          <div><dt>画面人数</dt><dd>{current ? status.persons ?? '—' : '—'}</dd></div>
          <div><dt>火焰候选</dt><dd>{current ? status.fire_candidates ?? '—' : '—'}</dd></div>
          <div><dt>姿态处理</dt><dd>{current ? status.fps ?? '—' : '—'} FPS</dd></div>
          <div><dt>火焰扫描</dt><dd>{!current ? '— FPS' : status.fire_state === 'error' ? '检测异常' : status.fire_state === 'stale' ? '检测结果已过期' : `${status.fire_fps ?? '—'} FPS`}</dd></div>
        </dl>
        <dl className="live-camera-summary">
          <div><dt>候选复核</dt><dd>{typeof status?.review_enabled !== 'boolean' ? '待获取' : status.review_enabled ? '已启用' : '未启用'}</dd></div>
          <div><dt>本地录像</dt><dd role="status">{pollError ? '状态待更新' : recordingStatus ? recordingStateName[recordingStatus.state] || `未知状态（${recordingStatus.state}）` : '待获取'}</dd></div>
          <div><dt>报警传送</dt><dd>{pollError ? '状态待更新' : loading ? '读取中' : status?.alerts?.error ? '传送异常' : status?.alerts?.enabled ? `待传送 ${status.alerts.pending ?? 0} 条${status.alerts.worker_alive ? '' : ' · 传送线程未运行'}` : '未就绪'}</dd></div>
        </dl>
        {status?.review_error && <p className="error" role="alert">复核异常：{status.review_error}</p>}
        {recordingStatus?.error && <p className="error" role="alert">录像错误：{recordingStatus.error}</p>}
        {status?.alerts?.error && <p className="error" role="alert">报警传送错误：{status.alerts.error}</p>}
        <p className="muted">人物全身入镜，保持腿部和地面可见。</p>
      </div>
    </div></div>
    <div className="live-camera-options">
      <details><summary>检测详情</summary><div className="live-camera-option-content">
        {pollError && <p>连接已中断，以下为最近一次读取的检测信息。</p>}
        <dl>
          <div><dt>安全流程</dt><dd>{status?.safety_pipeline === 'small_models_then_qwen' ? '小模型候选 → Qwen 复核' : status?.safety_pipeline ? '未知流程' : '未下发'}</dd></div>
          <div><dt>复核模型</dt><dd>{status?.review_model || '未下发'}</dd></div>
          <div><dt>云端复核配置</dt><dd>{typeof status?.cloud_calls !== 'boolean' ? '未下发' : status.cloud_calls ? '已配置' : '未配置'}</dd></div>
          <div><dt>姿态推理</dt><dd>{status?.inference_ms ?? '—'} ms</dd></div>
          <div><dt>火焰推理</dt><dd>{status?.fire_inference_ms ?? '—'} ms</dd></div>
          <div><dt>源画面分辨率</dt><dd>{status?.width ? `${status.width} × ${status.height}` : '—'}</dd></div>
          <div><dt>预览分辨率</dt><dd>{status?.preview_width ? `${status.preview_width} × ${status.preview_height}` : '—'}</dd></div>
          <div><dt>运行设备</dt><dd>{status?.device || '待加载'}</dd></div>
          <div><dt>火焰扫描方式</dt><dd>{loading ? '读取中' : status?.flame_tiling_enabled ? '全画面 + 高清重叠切片' : '全画面'}</dd></div>
        </dl>
        <div className="live-camera-conditions">
          <div><h3>求助条件</h3><p>{loading ? '读取中' : typeof status?.help_facing_camera_required !== 'boolean' ? '开启摄像头后读取' : status.help_facing_camera_required ? `双臂举高伸展、面向镜头并保持。面部朝向候选 ${status.help_face_candidates ?? 0} / ${status.persons ?? 0}。` : '双臂举高伸展、双手离开头部并保持。'}</p></div>
          <div><h3>跌倒识别</h3>{status?.fall_diagnostics?.length ? <ul>{status.fall_diagnostics.map(item => <li key={item.track_id}>{item.track_id}：{fallStateName[item.state] || '正在观察姿态'}</li>)}</ul> : <p>暂无跟踪人物</p>}</div>
        </div>
      </div></details>
      <details><summary>录像与存储</summary><div className="live-camera-option-content">
        {pollError && <p>连接已中断，以下为最近一次读取的录像与存储信息。</p>}
        <p>启用录像后，随预览开始和停止；关闭工作台约 30 秒后停止。保存原始视频，无音频。</p>
        {!recordingStatus ? <p>开启摄像头后读取存储配置。</p> : <dl>
          <div><dt>录像开关</dt><dd>{recordingStatus.enabled ? '已启用' : '未启用'}</dd></div>
          <div><dt>正在录制</dt><dd>{typeof status?.recording === 'boolean' ? status.recording ? '是' : '否' : '未下发'}</dd></div>
          <div><dt>实际保存路径</dt><dd>{recordingStatus.directory || '未下发'}</dd></div>
          <div><dt>录像文件数</dt><dd>{recordingStatus.files ?? '未下发'}</dd></div>
          <div><dt>录像占用</dt><dd>{recordingStatus.bytes == null ? '未下发' : `${(recordingStatus.bytes / 1024 ** 3).toFixed(3)} GiB`}</dd></div>
          <div><dt>已清理文件数</dt><dd>{recordingStatus.deleted_files ?? '未下发'}</dd></div>
          <div><dt>保留天数</dt><dd>{recordingStatus.retention_days == null ? '未下发' : `${recordingStatus.retention_days} 天`}</dd></div>
          <div><dt>容量上限</dt><dd>{recordingStatus.max_gib == null ? '未下发' : `${recordingStatus.max_gib} GiB`}</dd></div>
          <div><dt>磁盘空闲保留</dt><dd>{recordingStatus.min_free_gib == null ? '未下发' : `${recordingStatus.min_free_gib} GiB`}</dd></div>
          <div><dt>分段时长</dt><dd>{recordingStatus.segment_seconds == null ? '未下发' : `${recordingStatus.segment_seconds} 秒`}</dd></div>
        </dl>}
      </div></details>
    </div>
    <p className="live-camera-privacy">云端复核使用候选证据画面；关闭工作台约 30 秒后停止监测。</p>
  </section>;
}
