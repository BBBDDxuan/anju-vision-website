export type AnalysisMode = 'safety' | 'objects' | 'all';
export type StartMode = Exclude<AnalysisMode, 'all'>;
export type SafetyScan = Partial<Record<'processed_frames' | 'total_frames' | 'scan_progress' | 'candidate_count' | 'reviewed' | 'confirmed' | 'rejected' | 'uncertain' | 'unavailable', number | null>> & { candidates?: unknown[] | number | null };
export type AnalysisJob = {
  analysis_mode?: AnalysisMode;
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
export function analysisPath(videoId: string, mode: AnalysisMode, resume = false, demo = false) {
  const base = `/api/videos/${encodeURIComponent(videoId)}`;
  // Resume always addresses the latest job; choosing a new tab must not change its mode.
  return resume ? `${base}/resume` : demo ? `${base}/reanalyze` : `${base}/analyze?mode=${mode}`;
}
export const isAnalysisRunning = (job: AnalysisJob | null) => !!job && ['queued', 'running'].includes(job.status);
const percent = (value: number | null | undefined) => typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : undefined;
const count = (value: number | null | undefined) => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : undefined;
const statusLabel: Record<string, string> = { queued: '等待分析', running: '分析中', paused: '已保存进度', completed: '分析完成', partial: '部分完成', failed: '分析失败', interrupted: '分析中断' };
const stageLabel: Record<string, string> = { safety_scan: '本地小模型扫描', scanning: '本地小模型扫描', safety_review: 'Qwen 候选复核', reviewing: 'Qwen 候选复核', object_analysis: 'Qwen 物体分析', completed: '处理结束', queued: '等待执行' };

export function VideoAnalysisControls({ mode, onModeChange, job, pending, ready, demo, onStart, onResume, onPause }: {
  mode: StartMode; onModeChange: (mode: StartMode) => void; job: AnalysisJob | null;
  pending: boolean; ready: boolean; demo: boolean;
  onStart: () => void; onResume: () => void; onPause: () => void;
}) {
  const running = isAnalysisRunning(job);
  const blocked = running || pending || !ready;
  return <section className="video-analysis" id="analysis-tools" aria-label="上传录像分析">
    {!demo && <div className="analysis-mode-tabs" role="group" aria-label="选择录像分析任务">
      <button type="button" aria-pressed={mode === 'safety'} disabled={running || pending} onClick={() => onModeChange('safety')}>安全分析</button>
      <button type="button" aria-pressed={mode === 'objects'} disabled={running || pending} onClick={() => onModeChange('objects')}>物体追踪</button>
    </div>}
    <p className="analysis-method" id="analysis-method">{demo ? '重新分析将创建案例副本；上传录像可选择安全分析或物体追踪。' : mode === 'safety'
      ? '本地小模型扫描摔倒、举手求助、可见明火 → Qwen 复核候选片段。'
      : '使用 Qwen 分析物体与位置变化，生成带时间证据的踪迹。'}</p>
    <div className="analysis-bar">
      <button type="button" className="primary" aria-describedby="analysis-method" onClick={onStart} disabled={blocked}>{pending ? '正在提交…' : running ? '当前录像任务进行中…' : demo ? '重新分析此案例' : `开始${modeLabel(mode)}`}</button>
      {running && <button type="button" className="text-button" onClick={onPause} disabled={pending || job?.pause_requested}>{job?.pause_requested ? '正在保存并暂停…' : '暂停当前任务'}</button>}
      {job?.resumable && !running && !demo && <button type="button" className="text-button" onClick={onResume} disabled={blocked}>继续上次{modeLabel(jobMode(job))}</button>}
    </div>
    <p className="analysis-task-note">同一视频一次只能运行一个任务。开始所选分析会新建任务；继续上次任务保留其原分析模式。</p>
    {demo && <p className="analysis-task-note">重新识别将创建你的分析副本；云端调用计入每日额度，原展示成果保留。</p>}
    <div className="analysis-job-status" role="status">{job ? `最近任务：${modeLabel(jobMode(job))} · ${statusLabel[job.status] || job.status}${job.stage ? ` · ${stageLabel[job.stage] || job.stage}` : ''}` : demo ? '内置案例已就绪，可回看证据或重新分析副本。' : '上传完成，选择一种分析后开始。'}</div>
    {job?.retained_analysis && <p className="analysis-task-note">保留历史分析：{modeLabel(job.retained_analysis.analysis_mode || 'all')}{job.retained_analysis.event_ids && ` · ${job.retained_analysis.event_ids.length} 条事件`}{job.retained_analysis.run_id && ` · 来源任务 ${job.retained_analysis.run_id}`}。这些保留事件未由本次任务重新识别或复核。</p>}
    {job && (running || job.resumable) && <div className="analysis-job-progress"><span>{modeLabel(jobMode(job))} · 总体进度{percent(job.progress) === undefined ? '待上报' : ` ${percent(job.progress)?.toFixed(1)}%`}</span><progress aria-label={`${modeLabel(jobMode(job))}总体进度`} max={100} value={percent(job.progress)} /></div>}
  </section>;
}

export function SafetyScanProgress({ safety }: { safety?: SafetyScan | null }) {
  const processed = count(safety?.processed_frames);
  const total = count(safety?.total_frames);
  const progress = total !== undefined && total > 0 && processed !== undefined ? percent(processed / total * 100) : percent(safety?.scan_progress);
  const values = { ...safety, candidates: count(safety?.candidate_count) ?? (Array.isArray(safety?.candidates) ? safety.candidates.length : safety?.candidates) };
  const counters = [ ['candidates', '候选'], ['reviewed', '已复核'], ['confirmed', '已确认'], ['rejected', '已排除'], ['uncertain', '不确定'], ['unavailable', '复核不可用'] ] as const;
  return <section className="safety-scan-progress" aria-label="安全扫描与复核进度">
    <strong>本地扫描 → 候选片段 Qwen 复核</strong>
    <p>扫描帧数：{processed ?? '待上报'} / {total ?? '待上报'}{progress !== undefined && ` · 扫描进度 ${progress.toFixed(1)}%`}</p>
    {progress !== undefined && <progress aria-label="本地小模型扫描进度" max={100} value={progress} />}
    <dl>{counters.map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{count(values[key]) ?? '待上报'}</dd></div>)}</dl>
  </section>;
}

export function SafetyAnalysisPlan({ plan }: { plan?: { daily_requests_remaining?: number; max_windows_per_batch?: number } | null } = {}) {
  return <details className="analysis-details"><summary>安全分析计划 <span>本地扫描 · 候选复核</span></summary><p className="analysis-plan">本地扫描摔倒、举手求助、可见明火，候选证据画面交由 Qwen 复核。暂停后可继续原任务。</p>{plan && <p className="analysis-plan">{count(plan.daily_requests_remaining) !== undefined && `今日剩余 ${plan.daily_requests_remaining} 次云端请求。`}{count(plan.max_windows_per_batch) !== undefined && `每批最多复核 ${plan.max_windows_per_batch} 个候选。`}复核次数按实际候选计算。</p>}</details>;
}
