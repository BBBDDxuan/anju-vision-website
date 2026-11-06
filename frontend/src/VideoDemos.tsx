import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from 'react';
import { Arrow } from './Brand';
import { Icon } from './ui';
import { demoRegistry, fallDemo, fallEvidence, evidenceRange, relativeTime, reviewLabel, type DemoCase } from './demoRegistry';
import { useDemoVideo } from './useDemoVideo';
import { EvidenceInspectorPanel } from './EvidenceInspectorPanel';

const icons = ['activity', 'help-gesture', 'flame'];

function CapabilityVideoCard({ demo, index, evidenceOpen, onOpenEvidence, onCloseEvidence }: { demo: DemoCase; index: number; evidenceOpen: boolean; onOpenEvidence: (button: HTMLButtonElement) => void; onCloseEvidence: () => void }) {
  const player = useDemoVideo();
  const [started, setStarted] = useState(false);
  const result = demo.modelResult;
  const finished = player.currentTime >= (player.duration || demo.durationMs / 1000) - .03;
  useEffect(() => { if (evidenceOpen) { player.pause(); player.ref.current?.load(); setStarted(false); } }, [evidenceOpen, player.pause]);
  const play = (replay = false) => {
    if (evidenceOpen) onCloseEvidence();
    setStarted(true);
    const start = replay || !started || finished ? 0 : player.currentTime;
    void player.command({ start, play: true });
  };
  return <article className="av-cap-card av-video-card av-reveal" data-case={demo.id} data-result-status={result.status} style={{ '--delay': `${index * 75}ms` } as CSSProperties} aria-labelledby={`cap-${demo.id}`}>
    <div className="av-card-media">
      <video ref={player.ref} src={demo.videoSrc} poster={demo.posterSrc} preload="metadata" muted playsInline aria-label={`${demo.title}实景测试视频`} {...player.videoEvents}/>
      <span className="av-real-footage">实景测试素材 · 静音</span>
      {!player.playing && !player.error && <button className="av-media-play" aria-label={`${demo.title}：${finished ? '重播场景' : '播放场景'}`} disabled={player.busy} onClick={() => play()}><Icon name={finished ? 'replay' : 'play'} size={24}/><span>{player.busy ? '正在加载视频' : finished ? '重播场景' : '播放场景'}</span></button>}
      {player.error && <p className="av-media-error" role="alert">{player.error}</p>}
    </div>
    <div className="av-cap-copy">
      <div className="av-card-title"><Icon name={icons[index]} size={24}/><span>0{index + 1}</span></div>
      <h3 id={`cap-${demo.id}`}>{demo.id === 'fall' ? '跌倒 / 异常姿态识别' : demo.id === 'help' ? '举手动作识别' : '明火异常识别'}</h3>
      <span className={`av-result-tag ${result.status === 'available' ? 'is-verified' : ''}`}>{result.status === 'available' ? '实景测试 · 已有模型复核' : '实景测试 · 识别结果待接入'}</span>
      <p className="av-card-description">{demo.id === 'fall' ? '智能识别人体跌倒、异常躺卧等姿态，自动标记疑似风险事件。' : demo.id === 'help' ? '捕捉画面中的举手动作，标记疑似求助场景。' : '检测画面中的可见光火焰，提示疑似火情风险。'}</p>
      <p className="av-card-caveat"><Icon name="help" size={14}/>{demo.id === 'fall' ? 'AI 仅提供疑似线索参考，最终情况请以原始画面确认为准。' : demo.id === 'help' ? '动作识别不代表真实求助，需结合实际画面判断。' : '当前样例为打火机火焰，不代表真实火情；仅识别火焰视觉特征，需结合场景确认。'}</p>
      {result.status === 'available' && <div className="av-card-result"><p>已保存{result.result.title}候选；{reviewLabel(result.result)}，{result.result.humanConfirmed ? '已人工确认' : '未经人工确认'}，建议核对原片。</p>{demo.id === 'fall' && <button className="av-card-evidence-link" aria-expanded={evidenceOpen} aria-controls="fall-evidence-inspector" onClick={event => onOpenEvidence(event.currentTarget)}>查看 AI 分析依据<Arrow/></button>}</div>}
      <div className="av-card-actions">
        <button className="av-text-link" disabled={player.busy || !!player.error} onClick={() => player.playing ? player.pause() : play()} aria-label={`${demo.title}：${player.playing ? '暂停场景' : finished ? '重播场景' : '播放场景'}`}><Icon name={player.playing ? 'pause' : finished ? 'replay' : 'play'} size={15}/>{player.playing ? '暂停场景' : finished ? '重播场景' : '播放场景'}</button>
        {started && <button className="av-card-replay" aria-label={`${demo.title}：从头重播`} disabled={player.busy || !!player.error} onClick={() => play(true)}><Icon name="replay" size={15}/>重播</button>}
      </div>
    </div>
  </article>;
}

export function CapabilityVideos() {
  const [open, setOpen] = useState(false);
  const [present, setPresent] = useState(false);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scrollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const close = () => {
    document.dispatchEvent(new Event('av-demo-pause'));
    if (timer.current) clearTimeout(timer.current);
    if (scrollTimer.current) clearTimeout(scrollTimer.current);
    setOpen(false);
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    timer.current = setTimeout(() => {
      setPresent(false);
      const button = trigger.current;
      if (!button) return;
      const rect = button.getBoundingClientRect();
      if (rect.top < 90 || rect.bottom > innerHeight) button.scrollIntoView({ block: 'center', behavior: reduced ? 'instant' : 'smooth' });
    }, reduced ? 0 : 240);
    trigger.current?.focus({ preventScroll: true });
  };
  const show = (button: HTMLButtonElement) => {
    document.dispatchEvent(new Event('av-demo-pause'));
    if (timer.current) clearTimeout(timer.current);
    if (scrollTimer.current) clearTimeout(scrollTimer.current);
    trigger.current = button; setPresent(true); setOpen(true);
    scrollTimer.current = setTimeout(() => {
      const heading = document.getElementById('fall-evidence-title');
      const panel = document.getElementById('fall-evidence-inspector');
      if (!heading || !panel) return;
      heading.focus({ preventScroll: true });
      const navigationBottom = document.querySelector('.av-header')?.getBoundingClientRect().bottom || 0;
      window.scrollTo({ top: window.scrollY + panel.getBoundingClientRect().top - navigationBottom - 16, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
    }, matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 360);
  };
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); if (scrollTimer.current) clearTimeout(scrollTimer.current); }, []);
  return <><div className="av-capability-grid av-video-grid">{demoRegistry.map((demo, index) => <CapabilityVideoCard key={demo.id} demo={demo} index={index} evidenceOpen={open} onOpenEvidence={show} onCloseEvidence={close}/>)}</div><div className={`av-evidence-expander${open ? ' is-open' : ''}`} inert={!open} aria-hidden={!open}><div>{present && <EvidenceInspectorPanel onClose={close}/>}</div></div></>;
}

const stages = [
  { icon: 'video', title: '原始视频留存', detail: '完整保留原始录像，锁定事件前后的完整画面。' },
  { icon: 'chip', title: '智能分析复核', detail: 'AI 模型初步筛查，提取风险候选，支持人工复核校验。' },
  { icon: 'document', title: '事件自动摘要', detail: '按时间线梳理关键事件，清晰呈现异常的来龙去脉。' },
  { icon: 'play', title: '证据定位回看', detail: '一键跳转对应视频片段，结合原片核实真实情况。' },
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
        <div className="av-workflow-panel-heading"><div><h3>先看原片，再做判断。</h3><p>完整保留事件前后原始录像，可自由播放、拖动时间轴，直观查看事件全过程。</p></div><span>真实模型分析导出</span></div>
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
        <div className="av-workflow-evidence"><h4>模型引用关键帧 · 点击定位原片</h4><div className="av-keyframe-strip" role="group" aria-label="模型结论引用关键帧">{evidence.frames.map((frame, index) => <button key={frame.id} style={{ '--frame-delay': `${index * 70}ms` } as CSSProperties} aria-label={`定位到 ${relativeTime(frame.ptsMs)} 关键帧`} aria-pressed={selectedFrame === frame.id} onClick={() => { setSelectedFrame(frame.id); void player.command({ start: frame.ptsMs / 1000 }); }}><img src={frame.image} alt={`原片相对时间 ${relativeTime(frame.ptsMs)}`} loading="lazy"/><time>{relativeTime(frame.ptsMs)}</time></button>)}</div></div>
      </div>
      <div className="av-stage-content" key={stage}>
        <p className="av-stage-eyebrow">CASE 01 <span>/ STAGE 0{stage + 1}</span></p>
        <h3>{stage === 0 ? '原始视频留存' : stages[stage].title}</h3>
        {stage === 0 && <>
          <p>完整保留事件前后原始录像，可自由播放、拖动时间轴，直观查看事件全过程。</p>
          <dl className="av-case-facts">
            <div><dt>案例</dt><dd>人物跌倒实景测试</dd></div>
            <div><dt>原片时长</dt><dd>{durationSec.toFixed(2)} 秒</dd></div>
            <div><dt>分析来源</dt><dd>AI 模型分析导出</dd></div>
            <div><dt>事件数量</dt><dd>1 条疑似跌倒线索</dd></div>
          </dl>
          <p className="av-stage-note">此处回放已有结果，不在官网发起实时模型推理。</p>
        </>}
        {stage === 1 && <>
          <p>{stages[stage].detail}</p>
          <div className="av-review-step"><span>LOCAL / 本地候选</span><strong>疑似跌倒</strong><p>候选触发于相对时间 {relativeTime(evidence.localCandidateTriggerSec * 1000)}，需要结合前后画面复核。</p></div>
          <div className="av-review-step"><span>QWEN / 视觉模型复核</span><strong>{reviewLabel(evidence)}</strong><p>模型描述约 5.00 秒开始失去支撑、约 6.00 秒已躺卧；这是模型说明，并非人工精确动作标注。</p></div>
          <button className="av-demo-action" disabled={player.busy || !!player.error} onClick={() => void player.command({ start: startSec, end: endSec })}><Icon name="clock" size={16}/>{range} 证据区间<Arrow/></button>
          <button className="av-secondary-action" disabled={player.busy || !!player.error} onClick={() => void player.command({ start: 5, end: endSec, play: true })}>查看约 5.00–6.30 秒的姿态变化<Arrow/></button>
          <p className="av-stage-note">未经人工确认；证据区间的起点不等于跌倒开始，也不能据此推断受伤。</p>
        </>}
        {stage === 2 && <>
          <p>{stages[stage].detail}</p>
          <p>一条事件，五帧依据。模型结论实际引用的五张原始关键帧常驻在播放器下方，可随时定位核对。</p>
          <div className="av-event-summary"><span>01 / {evidence.title}</span><strong>{range}</strong><p>模型复核支持跌倒过程，未经人工确认。区间包含事前站立与姿态变化，需结合原片判断。</p></div>
          <details className="av-model-reason"><summary>查看模型复核原文</summary><p>{evidence.reviewReason}</p></details>
        </>}
        {stage === 3 && <>
          <p>{stages[stage].detail}</p>
          <button className="av-demo-action" disabled={player.busy || !!player.error} onClick={() => void player.command({ start: startSec, end: endSec, play: true })}><Icon name="play" size={17}/>回看原片<Arrow/></button>
          <p>沿完整 {durationSec.toFixed(2)} 秒时间轴，回看这一条事件引用的证据窗口。</p>
          <div className="av-evidence-timeline" style={{ '--evidence-start': `${startSec / durationSec * 100}%`, '--evidence-width': `${(endSec - startSec) / durationSec * 100}%`, '--playhead': `${Math.min(100, player.currentTime / durationSec * 100)}%` } as CSSProperties}>
            <div className="av-time-track"><span className="av-evidence-window"/><span className="av-time-cursor"/></div>
            <label className="av-sr-only" htmlFor="workflow-video-position">定位原片相对时间</label>
            <input id="workflow-video-position" type="range" min="0" max={durationSec} step="0.01" value={player.currentTime} disabled={!!player.error} onChange={event => void player.command({ start: Number(event.target.value) })} aria-valuetext={relativeTime(player.currentTime * 1000)}/>
            <div className="av-time-labels"><time>00:00.00</time><span>证据窗口</span><time>{relativeTime(fallDemo.durationMs)}</time></div>
          </div>
          <div className="av-event-summary"><span>01 / 疑似跌倒</span><strong>{range}</strong><p>{reviewLabel(evidence)} · 未经人工确认</p></div>
          <p className="av-stage-note">从证据区间起点播放，到区间末尾自动暂停；可随时暂停或重播。</p>
        </>}
      </div>
    </div>
    <div className="av-stage-footer"><span>一个真实案例 · 四个查看阶段</span><div><button disabled={stage === 0} onClick={() => changeStage(stage - 1, true)}>上一阶段</button><button disabled={stage === 3} onClick={() => changeStage(stage + 1, true)}>下一阶段<Arrow/></button></div></div>
  </div>;
}
