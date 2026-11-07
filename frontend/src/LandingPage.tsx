import { AccountAvatar } from './AccountAvatar';
import { useEffect, useRef, useState, type CSSProperties, type MouseEvent } from 'react';
import { Icon } from './ui';
import { Brand, SectionLabel, Arrow } from './Brand';
import type { IntroductionPage } from './introduction';
import { workspaceHref } from './navigation';
import { CapabilityVideos, WorkflowDemo } from './VideoDemos';
import { demoRegistry, evidenceRange, relativeTime, type DemoCaseId } from './demoRegistry';
import './product-site.css';
import './video-demos.css';

export type DemoVideo = { id: string; name: string; duration_ms: number; record_count: number; priority_count: number; poster_url: string; description: string; case_type: string };
type Props = { page: IntroductionPage; onPageChange: (page: IntroductionPage) => void; onEnter: () => void; onCamera: () => void; demos?: DemoVideo[]; onOpenDemo?: (id: string) => void };
const sections = [{ id: 'home', label: '首页' }, { id: 'capabilities', label: '核心能力' }, { id: 'workflow', label: '工作流程' }, { id: 'ai-qa', label: 'AI 问答' }, { id: 'devices', label: '设备接入' }];
const homeImage = '/assets/home-care-watering.webp';
const qaExamples: { question: string; caseId: DemoCaseId; evidenceQuestion?: boolean }[] = [
  { question: '这段视频里有没有需要关注的异常？', caseId: 'fall' },
  { question: '有没有识别到挥手动作？', caseId: 'help' },
  { question: '有没有识别到明火？', caseId: 'fire' },
  { question: '帮我找到相关的视频片段。', caseId: 'fall', evidenceQuestion: true },
];

export function LandingPage({ onEnter, onCamera, demos = [], onOpenDemo }: Props) {
  const [active, setActive] = useState('home');
  const [scrolled, setScrolled] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [questionIndex, setQuestionIndex] = useState(0);
  const [evidenceExpanded, setEvidenceExpanded] = useState(false);
  const [evidencePresent, setEvidencePresent] = useState(false);
  const evidenceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const qa = qaExamples[questionIndex];
  const qaCase = demoRegistry.find(item => item.id === qa.caseId)!;
  const evidence = qaCase.modelResult.status === 'available' ? qaCase.modelResult.result : null;
  const waveResult = qaCase.modelResult.status === 'pose_based_heuristic' ? qaCase.modelResult.result : null;
  const evidenceFrame = evidence?.frames.find(frame => frame.ptsMs === 6000) || evidence?.frames[0];
  const answer = waveResult
    ? `检测到 ${waveResult.events.length} 条连续挥手动作${waveResult.events.length ? `，出现时间为 ${waveResult.events.map(evidenceRange).join('、')}` : ''}。`
    : evidence
    ? qa.evidenceQuestion
      ? `已找到相关片段：${evidenceRange(evidence)}。点击下方画面可查看事件详情。`
      : `${qa.caseId === 'fire' ? '检测到明火，请及时关注' : '检测到跌倒姿态，请及时关注'}。出现时间：${evidenceRange(evidence)}。`
    : '这段视频中暂时没有找到相关信息。';

  useEffect(() => {
    const update = () => {
      setScrolled(window.scrollY > 24);
      const threshold = Math.min(180, window.innerHeight * .25);
      let current = 'home';
      for (const section of sections) {
        if ((document.getElementById(section.id)?.getBoundingClientRect().top ?? Infinity) <= threshold) current = section.id;
      }
      setActive(current);
    };
    let ticking = false;
    const scroll = () => { if (!ticking) { ticking = true; requestAnimationFrame(() => { update(); ticking = false; }); } };
    window.addEventListener('scroll', scroll, { passive: true });
    window.addEventListener('resize', scroll);
    update();
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) if (entry.isIntersecting) { entry.target.classList.add('av-visible'); observer.unobserve(entry.target); }
    }, { threshold: .08 });
    root.current?.querySelectorAll('.av-reveal').forEach(element => observer.observe(element));
    const restoreSection = () => {
      const hash = location.hash.slice(1);
      const id = hash === 'how-it-works' ? 'workflow' : hash === 'overview' ? 'home' : hash;
      if (sections.some(section => section.id === id)) alignSection(id, 'instant');
    };
    let disposed = false;
    let initialScroll = requestAnimationFrame(restoreSection);
    const restoreAfterLayout = () => { cancelAnimationFrame(initialScroll); initialScroll = requestAnimationFrame(() => { if (!disposed) restoreSection(); }); };
    window.addEventListener('load', restoreAfterLayout);
    window.addEventListener('pageshow', restoreAfterLayout);
    void document.fonts.ready.then(() => { if (!disposed) restoreAfterLayout(); });
    window.addEventListener('popstate', restoreSection);
    window.addEventListener('hashchange', restoreSection);
    return () => {
      disposed = true;
      cancelAnimationFrame(initialScroll);
      window.removeEventListener('load', restoreAfterLayout);
      window.removeEventListener('pageshow', restoreAfterLayout);
      window.removeEventListener('scroll', scroll); window.removeEventListener('resize', scroll);
      window.removeEventListener('popstate', restoreSection); window.removeEventListener('hashchange', restoreSection);
      observer.disconnect();
    };
  }, []);
  useEffect(() => () => {
    if (evidenceTimer.current) clearTimeout(evidenceTimer.current);
  }, []);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setMenuOpen(false); };
    window.addEventListener('keydown', escape);
    return () => window.removeEventListener('keydown', escape);
  }, []);

  const alignSection = (id: string, behavior: ScrollBehavior) => {
    const target = document.getElementById(id);
    if (!target) return;
    const headerHeight = root.current?.querySelector('.av-header')?.getBoundingClientRect().height || 78;
    const inset = Number.parseFloat(getComputedStyle(target).paddingTop) || 0;
    window.scrollTo({ top: Math.max(0, window.scrollY + target.getBoundingClientRect().top + inset - headerHeight - 22), behavior });
  };
  const navigateToSection = (id: string, targetId = id) => {
    setMenuOpen(false);
    const url = `/#${id}`;
    if (`${location.pathname}${location.hash}` !== url) history.pushState(null, '', url);
    alignSection(targetId, matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth');
  };
  const scrollTo = (event: MouseEvent<HTMLAnchorElement>, id: string, targetId = id) => {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); navigateToSection(id, targetId);
  };
  const enter = (event: MouseEvent<HTMLAnchorElement>, camera = false) => {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); camera ? onCamera() : onEnter();
  };
  const chooseQuestion = (index: number) => {
    if (evidenceTimer.current) clearTimeout(evidenceTimer.current);
    setEvidenceExpanded(false); setEvidencePresent(false);
    setQuestionIndex(index);
  };
  const toggleEvidence = () => {
    if (evidenceTimer.current) clearTimeout(evidenceTimer.current);
    if (evidenceExpanded) {
      setEvidenceExpanded(false);
      if (matchMedia('(prefers-reduced-motion: reduce)').matches) setEvidencePresent(false);
      else evidenceTimer.current = setTimeout(() => setEvidencePresent(false), 220);
    } else { setEvidencePresent(true); setEvidenceExpanded(true); }
  };

  return <div className="av-site" ref={root}>
    <a className="av-skip" href="#main-content">跳到主要内容</a>
    <header className={`av-header${scrolled ? ' av-header-solid' : ''}`}>
      <div className="av-header-inner av-container">
        <a href="/#home" className="av-logo-link" aria-label="安居视界首页" onClick={event => scrollTo(event, 'home')}><Brand/></a>
        <nav className={`av-nav${menuOpen ? ' av-nav-open' : ''}`} id="av-navigation" aria-label="官网导航" style={{ '--nav-index': sections.findIndex(section => section.id === active) } as CSSProperties}>
          {sections.map(section => <a key={section.id} href={`/#${section.id}`} className={active === section.id ? 'is-active' : ''} aria-current={active === section.id ? 'location' : undefined} onClick={event => scrollTo(event, section.id)}>{section.label}</a>)}
          <span className="av-nav-line" aria-hidden="true"/>
        </nav>
        <a className="av-button av-button-small av-header-cta" href={workspaceHref('camera')} onClick={event => enter(event, true)}><Icon name="monitor" size={17}/>进入工作台<Arrow/></a>
        <button className="av-menu" aria-label={menuOpen ? '关闭菜单' : '打开菜单'} aria-controls="av-navigation" aria-expanded={menuOpen} onClick={() => setMenuOpen(!menuOpen)}><Icon name={menuOpen ? 'close' : 'menu'} size={25}/></button>
        <AccountAvatar />
      </div>
    </header>

    <main id="main-content" className="av-main" tabIndex={-1}>
      <section className="av-hero" id="home" aria-labelledby="hero-title">
        <div className="av-container av-hero-inner">
          <div className="av-hero-copy">
            <p className="av-eyebrow av-entrance"><span/>AI 视频理解 · 居家安全关怀</p>
            <h1 id="hero-title" className="av-entrance">让牵挂，<br/><em>有迹可循。</em></h1>
            <p className="av-hero-description av-entrance">不用守着监控，也不会错过需要注意的危险。AI 自动分析居家录像，提供跌倒、挥手和明火安全提醒，标记需要关注的时刻，让每一份关心都清晰可查。</p>
            <div className="av-actions av-entrance"><a className="av-button" href={workspaceHref('videos')} onClick={event => enter(event)}><Icon name="play" size={17}/>开始视频分析<Arrow/></a><a className="av-button av-button-outline" href="/#capabilities" onClick={event => scrollTo(event, 'capabilities')}>了解核心能力<Arrow down/></a></div>
            <div className="av-hero-footnote av-entrance"><Icon name="shield" size={15}/>为独居老人照护与家庭安全而设计</div>
          </div>
          <div className="av-hero-art" aria-label="居家安全场景示意">
            <img className="av-hero-image" src={homeImage} width="1448" height="1086" fetchPriority="high" alt="温暖客厅中，老人微笑着为花草浇水的居家生活画面"/>
            <div className="av-scene-tag"><Icon name="shield" size={14}/>居家安全<span>场景示意</span></div>
          </div>
        </div>
        <div className="av-container av-hero-principles" aria-label="产品价值">
          {[['video', '完整原片留存', '留住事件前后的完整画面'], ['clock', '异常智能定位', '快速找到需要关注的时刻'], ['document', '证据随时回看', '随时查看事件前后的完整画面']].map(([icon, title, description]) => <div className="av-value-card" key={title}><span><Icon name={icon} size={27}/></span><div><h2>{title}</h2><p>{description}</p></div></div>)}
        </div>
        <a className="av-hero-next" href="/#capabilities" aria-label="向下了解核心能力" onClick={event => scrollTo(event, 'capabilities')}><Arrow down/></a>
      </section>

      <section id="capabilities" className="av-section av-capabilities" aria-labelledby="capabilities-title">
        <div className="av-container">
          <div className="av-section-top av-reveal"><div><SectionLabel number="01 / CAPABILITIES">核心能力</SectionLabel><h2 id="capabilities-title">风险线索，<em>精准捕捉。</em></h2></div><p>自动筛查居家风险场景，省去逐帧翻找录像的麻烦。</p></div>
          <p className="av-demo-intro">以下为已保存的离线分析演示，展示识别结果与画面标记。</p>
          <CapabilityVideos/>
        </div>
      </section>

      <section id="workflow" className="av-section av-workflow" aria-labelledby="workflow-title">
        <div className="av-container">
          <div className="av-section-top av-reveal"><div><SectionLabel number="02 / WORKFLOW">工作流程</SectionLabel><h2 id="workflow-title">完整链路，<em>还原始末。</em></h2></div><p>从原始录像到事件结论，每一步都清晰可追溯。</p></div>
          <WorkflowDemo/>
        </div>
      </section>

      <section id="ai-qa" className="av-section av-qa" aria-labelledby="qa-title">
        <div className="av-container av-qa-layout">
          <div className="av-qa-copy av-reveal"><SectionLabel number="03 / ASK WITH AI">AI 问答</SectionLabel><h2 id="qa-title">开口即查，<br/><em>答必有据。</em></h2><p>不用耗费时间翻找长录像，用日常对话就能查询居家情况。所有回答均关联对应视频片段，结论清晰、可查可验。</p><div className="av-example-questions" role="group" aria-label="选择示例问题">{qaExamples.map((example, index) => <button key={example.question} aria-pressed={questionIndex === index} onClick={() => chooseQuestion(index)}><Icon name="chat" size={17}/><span>{example.question}</span><Arrow/></button>)}</div></div>
          <div className="av-chat-demo av-reveal">
            <div className="av-chat-heading"><span><span className="av-ai-avatar"><Icon name="spark" size={25}/></span><strong>安居 AI<small>基于 AI 分析结果生成回答</small></strong></span><span className="av-chat-demo-label">预设问答演示</span></div>
            <div className="av-chat-content"><div className="av-user-message"><span>{qa.question}</span><span className="av-user-avatar"><Icon name="user" size={17}/></span></div><div className="av-ai-message"><span className="av-ai-avatar"><Icon name="spark" size={17}/></span><div className="av-answer-bubble" aria-live="polite">
              <p>{answer}</p>
              {evidence && evidenceFrame && <>
                <button className="av-qa-evidence" aria-label={`${evidenceExpanded ? '收起' : '展开'}${evidence.title}证据详情`} aria-expanded={evidenceExpanded} aria-controls="av-qa-evidence-detail" onClick={toggleEvidence}>
                  <span className="av-qa-thumb"><img src={evidenceFrame.image} alt="相关原始视频画面" loading="lazy" width="720" height="480"/><Icon name="document" size={19}/></span>
                  <span><small>相关证据 · 原片关键帧</small><strong>{evidence.title}</strong><span><Icon name="clock" size={13}/>{evidenceRange(evidence)}</span></span>
                  <span className="av-qa-disclosure" aria-hidden="true"><Arrow down/></span>
                </button>
                <div id="av-qa-evidence-detail" className={`av-inline-evidence${evidenceExpanded ? ' is-expanded' : ' is-closing'}`} role="region" aria-label="示例证据详情" hidden={!evidencePresent} inert={!evidenceExpanded} aria-hidden={!evidenceExpanded}>
                  {evidencePresent && <>
                    <figure><img src={evidenceFrame.image} alt={`原始画面 ${relativeTime(evidenceFrame.ptsMs)}`} width="720" height="480"/><figcaption>真实原片关键帧 · 已保存的分析画面</figcaption></figure>
                    <div className="av-inline-evidence-meta"><span>{qaCase.title}实景测试原片</span><time>相对视频时间 {relativeTime(evidenceFrame.ptsMs)}</time></div>
                    <p>出现时间：{evidenceRange(evidence)}。</p>
                  </>}
                </div>
              </>}
              <p className="av-answer-source"><Icon name="layers" size={12}/>{waveResult ? '来源：已保存的离线动作分析 · 预设示例' : evidence ? '来源：已保存的 AI 分析 · 预设示例' : '暂无可用分析结果'}</p>
            </div></div></div>
            <p className="av-chat-note"><Icon name="chat" size={14}/><span>预设问答演示 · 使用已保存的分析结果，不进行实时分析</span></p>
          </div>
        </div>
      </section>

      <section id="devices" className="av-section av-devices" aria-labelledby="devices-title">
        <div className="av-container av-device-layout">
          <div className="av-device-copy av-reveal"><SectionLabel number="04 / CONNECTION">设备接入</SectionLabel><h2 id="devices-title">熟悉设备，<br/><em>轻松接入。</em></h2><p>无需更换现有设备，支持多种视频源接入。把画面交给 AI 值守，把时间留给家人陪伴。</p><small>接入与分析能力以实际部署、后端服务及模型配置为准。</small></div>
          <div className="av-camera-illustration av-reveal"><div className="av-camera-halo"/><img src="/assets/camera.svg" width="340" height="390" loading="lazy" alt="家用云台摄像头外观示意，不代表特定品牌兼容性"/><span>家用摄像头 · 外观示意</span></div>
          <div className="av-device-features av-reveal">
            <a className="av-connection-card" href={workspaceHref('camera')} onClick={event => enter(event, true)}><span><Icon name="plug" size={27}/></span><div><h3>本地摄像头</h3><p>调用本地摄像头服务，即可查看实时画面、开启 AI 分析。</p><small>需本地服务与模型就绪</small></div><Arrow/></a>
            <a className="av-connection-card" href={workspaceHref('videos')} onClick={event => enter(event)}><span><Icon name="video" size={27}/></span><div><h3>导入 MP4 录像</h3><p>上传已有监控录像文件，手动启动 AI 分析与证据整理。</p></div><Arrow/></a>
            <div className="av-connection-card" aria-label="摄像头协议接入，按部署配置，需后端支持"><span><Icon name="shield" size={27}/></span><div><h3>摄像头协议接入</h3><p>支持按实际部署对接网络摄像头，具体协议及云端分析能力以配置为准。</p><small>按部署配置 / 需后端支持</small></div></div>
          </div>
        </div>
      </section>

      <section className="av-final-cta"><div className="av-container av-reveal"><p className="av-eyebrow">ANJU VISION · 让关心有据可查</p><h2>距离可以很远，<em>关心可以更近。</em></h2><p>让 AI 留意需要关注的瞬间，让家人的安全多一份守护。</p><a className="av-button" href={workspaceHref('camera')} onClick={event => enter(event, true)}>进入工作台<Arrow/></a>{demos.length > 0 && <div className="av-real-demos"><span>已有保存案例</span>{demos.slice(0, 3).map(demo => <button key={demo.id} onClick={() => onOpenDemo?.(demo.id)}>{demo.name.replace('.mp4', '').slice(0, 26)}<Arrow/></button>)}</div>}</div></section>
    </main>
    <footer className="av-footer"><div className="av-container"><Brand compact/><span>独居老人照护与家庭安全 · AI 视频分析平台</span><a href="/#home" onClick={event => scrollTo(event, 'home')}>回到顶部<Arrow/></a></div></footer>
  </div>;
}
