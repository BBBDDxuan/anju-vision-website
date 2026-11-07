export type AnalysisMode = 'safety' | 'objects' | 'all';
export type StartMode = Exclude<AnalysisMode, 'all'>;
export type FlameScan = 'normal' | 'small_flame';
export type SafetyScan = Partial<Record<'processed_frames' | 'total_frames' | 'scan_progress' | 'candidate_count' | 'reviewed' | 'confirmed' | 'rejected' | 'uncertain' | 'unavailable' | 'duplicate', number | null>> & { candidates?: unknown[] | number | null };
export type AnalysisJob = {
  analysis_mode?: AnalysisMode;
  flame_scan?: FlameScan;
  status: string;
  stage: string;
  progress: number;
  resumable?: boolean;
  pause_requested?: boolean;
  safety?: SafetyScan | null;
  retained_analysis?: { run_id?: string; analysis_mode?: AnalysisMode; event_ids?: string[] } | null;
};

export const modeLabel = (mode: AnalysisMode) => ({ safety: '安全分析', objects: '物体追踪', all: '安全与物体联合分析' })[mode];
export const jobMode = (job: Pick<AnalysisJob, 'analysis_mode'> | null): AnalysisMode => job?.analysis_mode || 'all';
export const planPath = (videoId: string, mode: AnalysisMode) => `/api/videos/${encodeURIComponent(videoId)}/analysis-plan?mode=${mode}`;
export function analysisPath(videoId: string, mode: AnalysisMode, resume = false, demo = false, flameScan: FlameScan = 'normal') {
  const base = `/api/videos/${encodeURIComponent(videoId)}`;
  // Resume always addresses the latest job; choosing a new tab must not change its mode.
  const scan = mode !== 'objects' && flameScan === 'small_flame' ? '&flame_scan=small_flame' : '';
  return resume ? `${base}/resume` : demo ? `${base}/reanalyze` : `${base}/analyze?mode=${mode}${scan}`;
}
export const isAnalysisRunning = (job: AnalysisJob | null) => !!job && ['queued', 'running'].includes(job.status);
const percent = (value: number | null | undefined) => typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : undefined;
const statusLabel: Record<string, string> = { queued: '等待分析', running: '正在分析', paused: '进度已保存', completed: '分析完成', partial: '部分完成', failed: '分析未完成', interrupted: '分析已中断' };

export function FlameScanSetting({ value, onChange, disabled }: {
  value: FlameScan; onChange: (scan: FlameScan) => void; disabled: boolean;
}) {
  return <div className="flame-scan-row"><label className="flame-scan-setting">明火识别
    <select value={value} onChange={e => onChange(e.target.value as FlameScan)} disabled={disabled} aria-describedby="flame-scan-note"><option value="normal">标准识别</option><option value="small_flame">留意小火苗</option></select>
  </label><p id="flame-scan-note">小火苗较难看清时可切换，分析可能需要更长时间。</p></div>;
}

export function VideoAnalysisControls({ mode, onModeChange, job, pending, ready, demo, onStart, onResume, onPause }: {
  mode: StartMode; onModeChange: (mode: StartMode) => void; job: AnalysisJob | null;
  pending: boolean; ready: boolean; demo: boolean;
  onStart: () => void; onResume: () => void; onPause: () => void;
}) {
  const running = isAnalysisRunning(job);
  const blocked = running || pending || !ready;
  return <section className="video-analysis" id="analysis-tools" aria-label="上传录像分析">
    <div className="analysis-primary-row">
    {!demo && <div className="analysis-mode-tabs" role="group" aria-label="选择录像分析任务">
      <button type="button" aria-pressed={mode === 'safety'} disabled={running || pending} onClick={() => onModeChange('safety')}>安全分析</button>
      <button type="button" aria-pressed={mode === 'objects'} disabled={running || pending} onClick={() => onModeChange('objects')}>物体追踪</button>
    </div>}
    <div className="analysis-bar">
      <button type="button" className="primary analysis-start" aria-describedby="analysis-method" onClick={onStart} disabled={blocked} aria-busy={running || pending}>{pending ? '正在提交…' : running ? '正在分析…' : demo ? '重新分析此案例' : mode === 'objects' ? '开始物体追踪' : '开始安全分析'}</button>
      {running && <button type="button" className="text-button" onClick={onPause} disabled={pending || job?.pause_requested}>{job?.pause_requested ? '正在保存并暂停…' : '暂停当前任务'}</button>}
      {job?.resumable && !running && !demo && <button type="button" className="text-button" onClick={onResume} disabled={blocked}>继续上次{modeLabel(jobMode(job))}</button>}
    </div>
    </div>
    <div className="analysis-feedback">
    <p className="analysis-method" id="analysis-method">{running ? jobMode(job) === 'objects' ? '正在整理视频中人物和物品的出现位置……' : '正在分析视频中的人物和环境安全情况……' : demo ? '重新分析将创建案例副本，保留现有展示结果。' : !job ? '录像已准备好，点击上方按钮开始。' : mode === 'safety' ? '留意跌倒姿态、挥手动作和明火，提供安全提醒。' : '回顾人物和物品在录像中的位置与活动。'}</p>
    {!ready && <p className="warning">分析服务暂未就绪，仍可上传和播放录像。</p>}
    <div className="analysis-job-status" role="status">{job ? `${modeLabel(jobMode(job))} · ${statusLabel[job.status] || '正在准备'}` : ''}</div>
    {job?.retained_analysis && <p className="analysis-task-note">已保留之前的{modeLabel(job.retained_analysis.analysis_mode || 'all')}结果，可继续回看。这些结果来自之前的分析。</p>}
    <div className="analysis-progress-slot">{job && running && <div className="analysis-job-progress"><span>分析进度{percent(job.progress) === undefined ? '正在更新' : ` ${percent(job.progress)?.toFixed(0)}%`}</span><progress aria-label="视频分析进度" max={100} value={percent(job.progress)} /></div>}</div>
    </div>
  </section>;
}

export function SafetyScanProgress({ safety }: { safety?: SafetyScan | null }) {
  const processed = safety?.processed_frames, total = safety?.total_frames;
  const progress = typeof total === 'number' && total > 0 && typeof processed === 'number' ? percent(processed / total * 100) : percent(safety?.scan_progress);
  return <p className="safety-scan-progress">{progress === undefined ? '正在读取画面，请稍候。' : `已读取 ${progress.toFixed(0)}% 的视频画面，分析结果会陆续显示。`}</p>;
}

export function SafetyAnalysisPlan({ plan }: { plan?: { daily_requests_remaining?: number; max_windows_per_batch?: number } | null } = {}) {
  return <details className="analysis-details"><summary>分析使用说明</summary><p>分析过程中可以暂停并保存进度，稍后继续。完成后可查看安全提醒、提问或播放相关片段。</p>{typeof plan?.daily_requests_remaining === 'number' && <p>今日剩余分析额度：{plan.daily_requests_remaining} 次。达到上限后会保存进度。</p>}</details>;
}
