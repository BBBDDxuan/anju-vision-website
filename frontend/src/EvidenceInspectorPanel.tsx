import { useState } from 'react';
import { Icon } from './ui';
import { fallDemo, fallEvidence, relativeTime } from './demoRegistry';
import { useDemoVideo } from './useDemoVideo';
import { EvidenceTimebar } from './EvidenceTimebar';
import './evidence-inspector.css';

export function EvidenceInspectorPanel({ onClose }: { onClose: () => void }) {
  const player = useDemoVideo();
  const [selectedFrame, setSelectedFrame] = useState('');
  const [feedback, setFeedback] = useState('请选择完整录像、证据区间或关键帧。');
  const [missing, setMissing] = useState<string[]>([]);
  const [mode, setMode] = useState<'full' | 'evidence'>('full');
  const frames = fallEvidence.frames.filter(frame => [2300, 5000, 6000].includes(frame.ptsMs));
  const total = player.duration || fallDemo.durationMs / 1000;
  const locate = async (start: number, end?: number, play = false) => {
    setMode(end === undefined ? 'full' : 'evidence');
    setFeedback('正在加载并定位画面…');
    const success = await player.command({ start, end, play });
    if (success) setFeedback(`已定位至 ${relativeTime(start * 1000)}${play ? end ? ' · 播放至证据末尾自动暂停' : ' · 播放完整录像' : ' · 已暂停，可核对画面'}`);
  };
  return <section className="av-evidence-inspector" id="fall-evidence-inspector" aria-labelledby="fall-evidence-title" onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); onClose(); } }}>
    <header className="av-inspector-heading"><div><p>AI EVIDENCE / 实景预分析案例</p><h3 id="fall-evidence-title" tabIndex={-1}><Icon name="shield" size={22}/>疑似跌倒 · AI 分析证据</h3></div><span className="av-inspector-status">模型复核支持</span><button className="av-inspector-close" aria-label="关闭证据面板" onClick={onClose}><Icon name="close" size={20}/></button></header>
    <div className="av-inspector-columns">
      <div className="av-inspector-media"><p className="av-inspector-eyebrow">A / 原始视频与证据</p>
        <div className="av-inspector-screen"><video ref={player.ref} src={fallDemo.videoSrc} poster={fallDemo.posterSrc} preload="metadata" muted playsInline aria-label="跌倒案例证据播放器" {...player.videoEvents}/><span className="av-real-footage">真实录像 · 静音</span></div>
        <EvidenceTimebar durationSec={total} timeSec={player.currentTime} ranges={[{ id: 'fall-evidence', startMs: fallEvidence.startMs, endMs: fallEvidence.endMs, label: '引用区间 2.30–6.30 秒', selected: true }]} onSeek={time => { setSelectedFrame(''); void locate(time); }} label="证据面板视频时间定位"/>
        <div className="av-inspector-controls"><button onClick={() => { setSelectedFrame(''); void locate(0, undefined, true); }} disabled={player.busy || !!player.error}><Icon name="play" size={15}/>播放完整录像</button><button onClick={() => { setSelectedFrame(''); void locate(2.3, 6.3, true); }} disabled={player.busy || !!player.error}><Icon name="clock" size={15}/>定位证据区间</button><button onClick={() => player.playing ? player.pause() : void player.command({ start: mode === 'evidence' && player.currentTime >= 6.3 ? 2.3 : undefined, end: mode === 'evidence' ? 6.3 : undefined, play: true })} disabled={player.busy || !!player.error}><Icon name={player.playing ? 'pause' : 'play'} size={15}/>{player.playing ? '暂停' : '继续播放'}</button></div>
        <p className="av-inspector-feedback" role={player.error ? 'alert' : 'status'}>{player.error || (player.busy ? '正在加载并定位画面…' : player.buffering ? '正在缓冲视频…' : feedback)}</p>
        <div className="av-inspector-frame-heading"><strong>模型引用关键画面</strong><span>点击暂停定位</span></div>
        <div className="av-inspector-frames">{frames.map((frame, i) => <button key={frame.id} style={{ animationDelay: `${i * 80}ms` }} aria-label={`核对 ${relativeTime(frame.ptsMs)} 关键帧`} aria-pressed={selectedFrame === frame.id} disabled={missing.includes(frame.id)} onClick={() => { setSelectedFrame(frame.id); void locate(frame.ptsMs / 1000); }}>{missing.includes(frame.id) ? <span className="av-frame-missing">该关键帧暂不可用</span> : <img src={frame.image} alt={`真实跌倒录像 ${relativeTime(frame.ptsMs)} 模型引用帧`} onError={() => setMissing(ids => [...ids, frame.id])}/>}<time>{relativeTime(frame.ptsMs)}</time><span>{i === 0 ? '事前对照' : i === 1 ? '模型描述：失稳' : '模型描述：躺卧'}</span></button>)}</div>
      </div>
      <div className="av-inspector-report"><p className="av-inspector-eyebrow">B / AI 事件分析报告</p><h4>让每一条判断，有据可查。</h4><dl><div><dt>事件类型</dt><dd>疑似跌倒</dd></div><div><dt>证据区间</dt><dd>2.30–6.30 秒<small>原视频相对时间 · 引用区间</small></dd></div><div><dt>模型复核</dt><dd>模型复核支持跌倒判断</dd></div><div><dt>人工核对</dt><dd>尚无人工确认记录</dd></div></dl><div className="av-inspector-reason"><h5>模型复核说明</h5><p>{fallEvidence.reviewReason}</p></div><p className="av-inspector-boundary">证据起点不代表跌倒开始。模型结论未经人工确认，建议结合原片人工核对。</p><p className="av-inspector-source">已保存的真实模型导出 · 本页不发起推理</p><p className="av-inspector-source">需先将案例导入工作台，才能在工作台继续分析。</p><button className="av-inspector-collapse" onClick={onClose}>收起面板 <Icon name="chevron-up" size={15}/></button></div>
    </div>
  </section>;
}
