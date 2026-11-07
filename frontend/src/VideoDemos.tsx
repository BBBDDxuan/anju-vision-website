import { useRef, useState, type CSSProperties, type KeyboardEvent } from 'react';
import { Arrow } from './Brand';
import { Icon } from './ui';
import { demoRegistry, fallDemo, fallEvidence, evidenceRange, relativeTime, type DemoCase } from './demoRegistry';
import { useDemoVideo } from './useDemoVideo';
import { DetectionOverlay, FireDetectionOverlay, WaveDetectionOverlay } from './detection/DetectionOverlay';
import { fallDetectionDemo } from './detection/fallDemoData';
import { fireDetectionDemo } from './detection/fireDemoData';
import { waveDetectionDemo } from './detection/waveDemoData';
import './detection/fire-detection-overlay.css';
import './detection/wave-detection-overlay.css';

const icons = ['activity', 'help-gesture', 'flame'];

function CapabilityVideoCard({ demo, index }: { demo: DemoCase; index: number }) {
  const player = useDemoVideo();
  const [started, setStarted] = useState(false);
  const waveStatus = useRef<HTMLParagraphElement>(null);
  const result = demo.modelResult;
  const hasOverlay = demo.id === 'fall' || demo.id === 'fire' || demo.id === 'help';
  const wave = result.status === 'pose_based_heuristic' ? result.result : null;
  const segments = result.status === 'available' ? [result.result] : wave?.events || [];
  const recognition = demo.id === 'fall' ? '检测到跌倒姿态' : demo.id === 'help' ? '检测到连续挥手动作' : '检测到火焰区域';
  const finished = player.currentTime >= (player.duration || demo.durationMs / 1000) - .03;
  const play = (replay = false) => {
    setStarted(true);
    const start = replay || !started || finished ? 0 : player.currentTime;
    void player.command({ start, play: true });
  };
  const playSegment = (segment: { startMs: number; endMs: number }) => {
    setStarted(true);
    void player.command({ start: segment.startMs / 1000, end: segment.endMs / 1000, play: true });
  };
  return <article className={`av-cap-card av-video-card av-reveal${demo.id === 'fall' ? ' av-fall-card' : demo.id === 'fire' ? ' av-fire-card' : ' av-wave-card'}`} data-case={demo.id} data-result-status={result.status} style={{ '--delay': `${index * 75}ms` } as CSSProperties} aria-labelledby={`cap-${demo.id}`}>
    <div className="av-card-media">
      <video ref={player.ref} src={demo.videoSrc} poster={demo.posterSrc} preload="metadata" muted playsInline loop={hasOverlay} aria-label={`${demo.title}实景测试视频`} {...player.videoEvents}/>
      {demo.id === 'fall' && <DetectionOverlay videoRef={player.ref} sequence={fallDetectionDemo}/>}
      {demo.id === 'fire' && <FireDetectionOverlay videoRef={player.ref} sequence={fireDetectionDemo}/>}
      {demo.id === 'help' && <WaveDetectionOverlay videoRef={player.ref} sequence={waveDetectionDemo} statusRef={waveStatus}/>}
      {!player.playing && !player.error && <button className={`av-media-play${hasOverlay && started ? ' av-media-play-resume' : ''}`} aria-label={`${demo.title}：${finished ? '重播场景' : '播放场景'}`} disabled={player.busy} onClick={() => play()}><Icon name={finished ? 'replay' : 'play'} size={24}/><span>{player.busy ? '正在加载视频' : finished ? '重播场景' : '播放场景'}</span></button>}
      {player.playing && !player.error && <button className="av-media-pause" aria-label={`${demo.title}：暂停场景`} disabled={player.busy} onClick={() => player.pause()}><Icon name="pause" size={22}/><span>暂停场景</span></button>}
      {player.error && <p className="av-media-error" role="alert">{player.error}</p>}
    </div>
    {hasOverlay && <div className={demo.id === 'fall' ? 'av-fall-progress' : demo.id === 'help' ? 'av-wave-progress' : 'av-fire-progress'}><input type="range" min="0" max={demo.durationMs / 1000} step="0.01" value={Math.min(player.currentTime, demo.durationMs / 1000)} aria-label={demo.id === 'fall' ? '跌倒演示视频时间' : demo.id === 'help' ? '挥手演示视频时间' : '明火演示视频时间'} aria-valuetext={relativeTime(player.currentTime * 1000)} disabled={!!player.error} onInput={event => { setStarted(true); void player.command({ start: Number(event.currentTarget.value) }); }}/><time>{relativeTime(player.currentTime * 1000)} / {relativeTime(demo.durationMs)}</time></div>}
    <div className="av-cap-copy">
      <div className="av-card-title"><Icon name={icons[index]} size={24}/><span>0{index + 1}</span></div>
      <h3 id={`cap-${demo.id}`}>{demo.id === 'fall' ? '跌倒 / 异常姿态识别' : demo.id === 'help' ? '挥手动作识别' : '明火异常识别'}</h3>
      <p className="av-card-description">{demo.id === 'fall' ? '识别人体跌倒及异常躺卧姿态。' : demo.id === 'help' ? '识别连续挥手动作及手部运动轨迹。' : '识别画面中的火焰区域，及时发现明火风险。'}</p>
      {demo.id === 'help' && <span ref={waveStatus} className="av-sr-only" role="status">正在追踪手部动作</span>}
      <div className="av-card-result"><dl className="av-case-result"><div><dt>识别结果</dt><dd>{segments.length ? recognition : '暂未识别到相关事件'}</dd></div><div><dt>出现时间</dt><dd>{segments.length ? segments.map((segment, i) => <time key={i}>{evidenceRange(segment)}</time>) : '暂无'}</dd></div></dl></div>
      <div className="av-card-actions">
        {segments.map((segment, i) => <button key={i} className="av-segment-play" disabled={player.busy || !!player.error} onClick={() => playSegment(segment)} aria-label={`${demo.title}：播放相关片段${segments.length > 1 ? ` ${i + 1}` : ''}`}><Icon name="play" size={15}/>播放相关片段</button>)}
        <button className="av-text-link" disabled={player.busy || !!player.error} onClick={() => player.playing ? player.pause() : play()} aria-label={`${demo.title}：${player.playing ? '暂停场景' : finished ? '重播场景' : '播放场景'}`}><Icon name={player.playing ? 'pause' : finished ? 'replay' : 'play'} size={15}/>{player.playing ? '暂停场景' : finished ? '重播场景' : '播放场景'}</button>
        {started && <button className="av-card-replay" aria-label={`${demo.title}：从头重播`} disabled={player.busy || !!player.error} onClick={() => play(true)}><Icon name="replay" size={15}/>重播</button>}
      </div>
    </div>
  </article>;
}

export function FallDetectionCard() {
  return <CapabilityVideoCard demo={fallDemo} index={0}/>;
}

export function FireDetectionCard({ demo }: { demo: DemoCase }) {
  return <CapabilityVideoCard demo={demo} index={2}/>;
}

export function WaveDetectionCard({ demo }: { demo: DemoCase }) {
  return <CapabilityVideoCard demo={demo} index={1}/>;
}

export function CapabilityVideos() {
  return <div className="av-capability-grid av-video-grid">{demoRegistry.map(demo => demo.id === 'fall' ? <FallDetectionCard key={demo.id}/> : demo.id === 'fire' ? <FireDetectionCard key={demo.id} demo={demo}/> : <WaveDetectionCard key={demo.id} demo={demo}/>)}</div>;
}

const stages = [
  { icon: 'video', title: '原始视频留存', detail: '完整保留原始录像，锁定事件前后的完整画面。' },
  { icon: 'chip', title: '智能安全分析', detail: '分析人物活动与环境情况，找到值得留意的片段。' },
  { icon: 'document', title: '事件自动摘要', detail: '按时间线梳理关键事件，清晰呈现异常的来龙去脉。' },
  { icon: 'play', title: '证据定位回看', detail: '一键跳转对应视频片段，查看事件前后的完整画面。' },
];

export function WorkflowDemo() {
  const [stage, setStage] = useState(0);
  const [selectedFrame, setSelectedFrame] = useState<string | null>(null);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const player = useDemoVideo();
  const evidence = fallEvidence;
  const durationSec = fallDemo.durationMs / 1000;
  const startSec = evidence.startMs / 1000;
  const endSec = evidence.endMs / 1000;
  const range = evidenceRange(evidence);
  const changeStage = (next: number, focus = false) => {
    player.pause(); setStage(next);
    if (focus) tabs.current[next]?.focus({ preventScroll: true });
  };
  const keyNavigation = (event: KeyboardEvent, index: number) => {
    const next = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? (index + 1) % 4
      : event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? (index + 3) % 4
      : event.key === 'Home' ? 0 : event.key === 'End' ? 3 : null;
    if (next !== null) { event.preventDefault(); changeStage(next, true); }
  };
  const replay = () => void player.command({ start: stage === 0 ? 0 : startSec, end: stage === 0 ? undefined : endSec, play: true });
  const toggle = () => {
    if (player.playing) player.pause();
    else {
      const start = stage === 0 ? (player.currentTime >= durationSec - .03 ? 0 : player.currentTime)
        : player.currentTime < startSec || player.currentTime >= endSec - .03 ? startSec : player.currentTime;
      void player.command({ start, end: stage === 0 ? undefined : endSec, play: true });
    }
  };
  return <div className="av-workflow-demo">
    <div className="av-stage-tabs" role="tablist" aria-label="真实跌倒案例的四个阶段" style={{ '--stage': stage } as CSSProperties}>
      {stages.map((item, index) => <button key={item.title} role="tab" id={`workflow-tab-${index}`} aria-controls="workflow-stage-panel" aria-selected={stage === index} tabIndex={stage === index ? 0 : -1} ref={element => { tabs.current[index] = element; }} onClick={() => changeStage(index)} onKeyDown={event => keyNavigation(event, index)}>
        <span className="av-stage-top"><span className="av-step-icon"><Icon name={item.icon} size={23}/></span><span className="av-step-number">0{index + 1}</span></span>
        <strong>{item.title}</strong><span className="av-stage-detail">{item.detail}</span>
      </button>)}
      <span className="av-stage-indicator" aria-hidden="true"/>
    </div>
    <div className="av-workflow-panel" id="workflow-stage-panel" role="tabpanel" aria-labelledby={`workflow-tab-${stage}`} tabIndex={0}>
      <div className="av-workflow-player">
        <div className="av-workflow-panel-heading"><div><h3>需要关注的时刻，一键回看。</h3><p>完整保留事件前后原始录像，可自由播放、拖动时间轴，直观查看事件全过程。</p></div><span>已保存的 AI 分析</span></div>
        <div className="av-workflow-screen">
          <video ref={player.ref} src={fallDemo.videoSrc} poster={fallDemo.posterSrc} preload="metadata" controls muted playsInline aria-label="跌倒案例完整原始视频" {...player.videoEvents}/>
          <span className="av-real-footage">实景测试 · 原始录像</span>
          {player.error && <p className="av-media-error" role="alert">{player.error}</p>}
        </div>
        <div className="av-workflow-controls">
          <button onClick={toggle} disabled={player.busy || !!player.error} aria-label={player.playing ? '暂停案例视频' : '播放案例视频'}><Icon name={player.playing ? 'pause' : 'play'} size={17}/>{player.playing ? '暂停' : '播放'}</button>
          <button onClick={replay} disabled={player.busy || !!player.error}><Icon name="replay" size={15}/>重播{stage === 0 ? '原片' : '证据'}</button>
          <time aria-label="视频相对时间">{relativeTime(player.currentTime * 1000)} <span>/ {relativeTime(fallDemo.durationMs)}</span></time>
        </div>
        <p className="av-player-note">{player.busy ? '正在加载 / 定位视频…' : '视频相对时间 · 静音播放 · 已保存的分析案例'}</p>
        <div className="av-workflow-evidence"><h4>相关视频画面 · 点击定位原片</h4><div className="av-keyframe-strip" role="group" aria-label="相关视频画面">{evidence.frames.map((frame, index) => <button key={frame.id} style={{ '--frame-delay': `${index * 70}ms` } as CSSProperties} aria-label={`定位到 ${relativeTime(frame.ptsMs)} 关键帧`} aria-pressed={selectedFrame === frame.id} onClick={() => { setSelectedFrame(frame.id); void player.command({ start: frame.ptsMs / 1000 }); }}><img src={frame.image} alt={`原片相对时间 ${relativeTime(frame.ptsMs)}`} loading="lazy"/><time>{relativeTime(frame.ptsMs)}</time></button>)}</div></div>
      </div>
      <div className="av-stage-content" key={stage}>
        <p className="av-stage-eyebrow">CASE 01 <span>/ STAGE 0{stage + 1}</span></p>
        <h3>{stage === 0 ? '原始视频留存' : stages[stage].title}</h3>
        {stage === 0 && <>
          <p>完整保留事件前后原始录像，可自由播放、拖动时间轴，直观查看事件全过程。</p>
          <dl className="av-case-facts">
            <div><dt>案例</dt><dd>人物跌倒实景测试</dd></div>
            <div><dt>原片时长</dt><dd>{durationSec.toFixed(2)} 秒</dd></div>
            <div><dt>分析来源</dt><dd>已保存的 AI 分析</dd></div>
            <div><dt>事件数量</dt><dd>1 条跌倒提醒</dd></div>
          </dl>
          <p className="av-stage-note">此处回放已保存的结果，不进行实时分析。</p>
        </>}
        {stage === 1 && <>
          <p>{stages[stage].detail}</p>
          <div className="av-review-step"><span>识别到的动作</span><strong>检测到跌倒姿态</strong><p>出现时间：{range}。</p></div>
          <div className="av-review-step"><span>相关画面</span><strong>{evidence.frames.length} 张关键画面</strong><p>点击画面可定位人物站立和姿态变化的相关时刻。</p></div>
          <button className="av-demo-action" disabled={player.busy || !!player.error} onClick={() => void player.command({ start: startSec, end: endSec })}><Icon name="clock" size={16}/>{range} 播放相关片段<Arrow/></button>
          <button className="av-secondary-action" disabled={player.busy || !!player.error} onClick={() => void player.command({ start: 5, end: endSec, play: true })}>查看约 5.00–{endSec.toFixed(2)} 秒的姿态变化<Arrow/></button>
        </>}
        {stage === 2 && <>
          <p>{stages[stage].detail}</p>
          <p>这条提醒关联 {evidence.frames.length} 张原始画面，展示在播放器下方，可随时点击回看。</p>
          <div className="av-event-summary"><span>01 / 跌倒提醒</span><strong>{range}</strong><p>检测到跌倒姿态，请及时关注。相关片段已自动定位。</p></div>
          <details className="av-model-reason"><summary>查看片段说明</summary><p>已标记人物站立后出现跌倒姿态的相关画面。</p></details>
        </>}
        {stage === 3 && <>
          <p>{stages[stage].detail}</p>
          <button className="av-demo-action" disabled={player.busy || !!player.error} onClick={() => void player.command({ start: startSec, end: endSec, play: true })}><Icon name="play" size={17}/>回看原片<Arrow/></button>
          <p>沿完整 {durationSec.toFixed(2)} 秒时间轴，查看事件前后的画面。</p>
          <div className="av-evidence-timeline" style={{ '--evidence-start': `${startSec / durationSec * 100}%`, '--evidence-width': `${(endSec - startSec) / durationSec * 100}%`, '--playhead': `${Math.min(100, player.currentTime / durationSec * 100)}%` } as CSSProperties}>
            <div className="av-time-track"><span className="av-evidence-window"/><span className="av-time-cursor"/></div>
            <label className="av-sr-only" htmlFor="workflow-video-position">定位原片相对时间</label>
            <input id="workflow-video-position" type="range" min="0" max={durationSec} step="0.01" value={player.currentTime} disabled={!!player.error} onChange={event => void player.command({ start: Number(event.target.value) })} aria-valuetext={relativeTime(player.currentTime * 1000)}/>
            <div className="av-time-labels"><time>00:00.00</time><span>相关片段</span><time>{relativeTime(fallDemo.durationMs)}</time></div>
          </div>
          <div className="av-event-summary"><span>01 / 跌倒提醒</span><strong>{range}</strong><p>检测到跌倒姿态，请及时关注。</p></div>
          <p className="av-stage-note">从相关片段起点播放，到片段末尾自动暂停；可随时暂停或重播。</p>
        </>}
      </div>
    </div>
    <div className="av-stage-footer"><span>一个真实案例 · 四个查看阶段</span><div><button disabled={stage === 0} onClick={() => changeStage(stage - 1, true)}>上一阶段</button><button disabled={stage === 3} onClick={() => changeStage(stage + 1, true)}>下一阶段<Arrow/></button></div></div>
  </div>;
}
