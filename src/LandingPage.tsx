import { useEffect, useRef, type MouseEvent } from 'react';
import { Icon } from './ui';
import { HomeScene } from './HomeScene';
import { introductionPages, introductionHref, type IntroductionPage } from './introduction';
import './landing.css';
import './introduction.css';

export type DemoVideo = { id: string; name: string; duration_ms: number; record_count: number; priority_count: number; poster_url: string; description: string; case_type: string };
type Props = { page: IntroductionPage; onPageChange: (page: IntroductionPage) => void; onEnter: () => void; onCamera: () => void; demos?: DemoVideo[]; onOpenDemo?: (id: string) => void };

export function LandingPage({ page, onPageChange, onEnter, onCamera, demos = [], onOpenDemo }: Props) {
  const index = introductionPages.findIndex(item => item.id === page);
  const current = introductionPages[index];
  const panel = useRef<HTMLDivElement>(null);
  const previous = useRef(page);
  useEffect(() => {
    if (previous.current === page) return;
    previous.current = page;
    panel.current?.scrollTo({ top: 0 });
    panel.current?.focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: 'instant' });
  }, [page]);
  const follow = (event: MouseEvent<HTMLAnchorElement>, next: IntroductionPage) => {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); onPageChange(next);
  };
  const demoLinks = demos.length > 0 && <div className="intro-demo-links"><span>已有真实分析案例</span>{demos.map(demo => <button key={demo.id} onClick={() => onOpenDemo?.(demo.id)}>{demo.name.replace(' · 已保存真实分析.mp4','').replace('已保存真实模型分析 · ','').replace('.mp4','')} <Icon name="arrow" size={14}/></button>)}</div>;

  return <main className="landing-page intro-paged" id="main-content" tabIndex={-1}>
    <div ref={panel} className="intro-page-content" tabIndex={-1} aria-labelledby="intro-page-heading" data-intro-page={page}>
      {page === 'overview' && <section className="intro-overview">
        <div className="intro-overview-copy"><span className="intro-subject"><Icon name="shield" size={16}/>独居关怀与家庭安全</span><h1 id="intro-page-heading">多一份关注，<br/>让居家风险有迹可查。</h1><p className="intro-description">从家人的日常活动，到需要留意的异常。<br className="intro-desktop-break"/>关注摔倒、举手求助与可见明火，让每一条线索都能回到画面。</p><div className="intro-overview-actions"><button className="primary" onClick={onEnter}>导入视频分析<Icon name="arrow" size={18}/></button><a href={introductionHref('how-it-works')} onClick={event => follow(event, 'how-it-works')}>了解使用流程</a></div><ul className="intro-assurances"><li><Icon name="shield" size={16}/>重要事件优先呈现</li><li><Icon name="video" size={16}/>原片与关键帧可回看</li></ul>{demoLinks}</div>
        <HomeScene/>
      </section>}
      {page === 'capabilities' && <section className="intro-detail-page">
        <header className="intro-section-title"><span className="intro-subject">核心能力</span><h1 id="intro-page-heading">关注家人安全，也找回物品踪迹。</h1><p>识别人物异常、留意可见明火、查找物品，并通过视频证据核对线索。</p></header>
        <div className="intro-capability-grid">{[
          { icon: 'home', label: '人物异常', title: '独居老人关怀', text: '关注疑似摔倒与举手求助，保留发生过程和关键时间。', points: ['人物动作与连续过程', '候选事件的复核状态', '可定位的原片时间'] },
          { icon: 'shield', label: '家庭危险', title: '可见明火识别', text: '先发现可见明火候选，再结合证据画面复核与回看。', points: ['明火候选线索', '复核说明与模型依据', '证据不足或异常反馈'] },
          { icon: 'search', label: '物品追踪', title: '查找物品踪迹', text: '想知道物品最后在哪里出现？从录像中查找物品出现的位置与时间。', points: ['物品出现的位置与时间', '已有记录的物品问答', '关联原片与关键帧'] },
        ].map(item => <article className="intro-capability" key={item.title}><div className="intro-capability-top"><span className="intro-feature-icon"><Icon name={item.icon} size={25}/></span><span>{item.label}</span></div><h2>{item.title}</h2><p>{item.text}</p><ul>{item.points.map(point => <li key={point}><span aria-hidden="true"/> {point}</li>)}</ul></article>)}</div>
        <section className="intro-evidence" aria-labelledby="intro-evidence-heading">
          <div className="intro-evidence-heading"><div><h2 id="intro-evidence-heading">视频证据回溯</h2><p>人物异常、家庭危险与物品踪迹，都能回到原片核对。</p></div><button className="text-button" onClick={onEnter}>导入视频<Icon name="arrow" size={16}/></button></div>
          <ul>{[
            ['clock', '事件时间线'], ['video', '原片回放'], ['search', '证据关键帧'], ['chat', '已有记录的证据问答'],
          ].map(([icon, text]) => <li key={text}><Icon name={icon} size={18}/><span>{text}</span></li>)}</ul>
        </section>
        {demoLinks}
      </section>}
      {page === 'how-it-works' && <section className="intro-detail-page">
        <header className="intro-section-title"><span className="intro-subject">使用流程</span><h1 id="intro-page-heading">从一段录像，到有据可查的提醒。</h1><p>导入、分析、回看。按自己的节奏查看每一条线索。</p></header>
        <ol className="intro-workflow">{[
          { icon: 'upload', title: '导入居家录像', text: '选择或拖入单机位 MP4；也可以从录像库打开已有视频。', detail: '导入后先回放原片，再由你触发分析。', action: '选择录像' },
          { icon: 'shield', title: '选择分析任务', text: '选择“安全分析”或“物品追踪”，查看扫描与复核进度。', detail: '同一录像一次运行一个任务，支持暂停与继续。', action: '查看进度' },
          { icon: 'clock', title: '回看并确认线索', text: '从事件时间线定位原片、查看关键帧，或提问了解已有记录。', detail: '结合复核状态和证据理解事件，按需标记已知悉。', action: '回看证据' },
        ].map((item, i) => <li key={item.title}><div className="intro-step-heading"><span className="intro-step-number">{i + 1}</span><Icon name={item.icon} size={25}/></div><h2>{item.title}</h2><p>{item.text}</p><div className="intro-step-detail"><span>{item.action}</span><p>{item.detail}</p></div></li>)}</ol>
        <div className="intro-workflow-note"><Icon name="video" size={24}/><div><h2>实时看护，从本机摄像头入口开始</h2><p>在“实时摄像头”开启已配置的本机摄像头，查看画面、录像状态和本次会话事件。</p></div><button className="primary" onClick={onCamera}>打开实时摄像头<Icon name="arrow" size={16}/></button></div>
      </section>}
    </div>
    <footer className="intro-pagination" aria-label="介绍页翻页"><p><span className="intro-page-count">{index + 1}<span> / {introductionPages.length}</span></span><span>{current.summary}</span></p><div className="intro-pagination-actions"><button disabled={index === 0} onClick={() => onPageChange(introductionPages[index - 1].id)}><Icon name="arrow" size={16}/>上一页</button><button disabled={index === introductionPages.length - 1} onClick={() => onPageChange(introductionPages[index + 1].id)}>下一页<Icon name="arrow" size={16}/></button></div></footer>
  </main>;
}
