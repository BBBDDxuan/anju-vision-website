import { useEffect, useRef, useState, type CSSProperties, type MouseEvent } from 'react';
import { Icon } from './ui';
import { Brand, SectionLabel, Arrow } from './Brand';
import type { IntroductionPage } from './introduction';
import { workspaceHref } from './navigation';
import { CapabilityVideos, WorkflowDemo } from './VideoDemos';
import { demoRegistry, evidenceRange, relativeTime, reviewLabel, type DemoCaseId } from './demoRegistry';
import './product-site.css';
import './video-demos.css';

export type DemoVideo = { id: string; name: string; duration_ms: number; record_count: number; priority_count: number; poster_url: string; description: string; case_type: string };
type Props = { page: IntroductionPage; onPageChange: (page: IntroductionPage) => void; onEnter: () => void; onCamera: () => void; demos?: DemoVideo[]; onOpenDemo?: (id: string) => void };
const sections = [{ id: 'home', label: '首页' }, { id: 'capabilities', label: '核心能力' }, { id: 'workflow', label: '工作流程' }, { id: 'ai-qa', label: 'AI 问答' }, { id: 'devices', label: '设备接入' }];
const homeImage = '/assets/home-care.webp';
const fireImage = '/assets/kitchen-fire.webp';
const qaExamples: { question: string; caseId: DemoCaseId; evidenceQuestion?: boolean }[] = [
  { question: '这段视频里有没有需要关注的异常？', caseId: 'fall' },
  { question: '有没有识别到求助？', caseId: 'help' },
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
  const evidenceFrame = evidence?.frames.find(frame => frame.ptsMs === 6000) || evidence?.frames[0];
  const answer = evidence
    ? qa.evidenceQuestion
      ? `这条线索引用的证据区间为 ${evidenceRange(evidence)}。下方可展开模型实际引用的关键帧，也可以在工作流程的“证据回看”阶段观看原片。`
      : `已有分析保留了 1 条${evidence.title}线索，证据区间为 ${evidenceRange(evidence)}。${evidence.reviewVerdict === 'confirmed' ? '视觉模型复核支持这一判断' : '视觉模型复核尚不支持确认'}，未经人工确认；请结合原片核对，不能据此推断受伤。`
    : `${qaCase.disclaimer}当前尚无可用识别结果，不能据此判断“有”或“没有”。`;

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
      if (sections.some(section => section.id === id)) document.getElementById(id)?.scrollIntoView({ behavior: 'instant' });
    };
    const initialScroll = requestAnimationFrame(restoreSection);
    window.addEventListener('popstate', restoreSection);
    window.addEventListener('hashchange', restoreSection);
    return () => {
      cancelAnimationFrame(initialScroll);
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

  const navigateToSection = (id: string, targetId = id) => {
    setMenuOpen(false);
    const url = `/#${id}`;
    if (`${location.pathname}${location.hash}` !== url) history.pushState(null, '', url);
    document.getElementById(targetId)?.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
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
      </div>
    </header>

    <main id="main-content" className="av-main" tabIndex={-1}>
      <section className="av-hero" id="home" aria-labelledby="hero-title">
        <div className="av-hero-art" aria-label="居家安全识别场景演示">
          <img className="av-hero-image" src={homeImage} width="1536" height="1024" fetchPriority="high" alt="温暖客厅中，老人身体失衡，伸手扶向茶桌的示意画面"/>
          <div className="av-art-shade"/>
          <div className="av-detection"><span><Icon name="warning" size={17}/>疑似跌倒<small>画面示意</small></span><i/><i/><i/><i/></div>
          <div className="av-fire-inset"><img src={fireImage} alt="厨房可见明火的示意画面" width="1536" height="1024"/><span><Icon name="flame" size={15}/>明火风险</span></div>
          <div className="av-scene-tag"><i/>AI 场景演示<span>非实时检测</span></div>
          <div className="av-floating-note"><Icon name="shield" size={19}/><div>把异常变成可追溯的线索<small>关键事件 · 时间定位 · 视频证据</small></div></div>
        </div>
        <div className="av-container av-hero-inner">
          <div className="av-hero-copy">
            <p className="av-eyebrow av-entrance"><span/>AI 视频理解 · 居家安全关怀</p>
            <h1 id="hero-title" className="av-entrance">让牵挂，<br/><em>有迹可循。</em></h1>
            <p className="av-hero-description av-entrance">看见日常里，需要被看见的时刻。<br/>以 AI 分析居家录像，发现疑似跌倒、求助与明火等风险线索，用时间线与视频证据，让关心有据可查。</p>
            <div className="av-actions av-entrance"><a className="av-button" href={workspaceHref('videos')} onClick={event => enter(event)}><Icon name="play" size={17}/>开始视频分析<Arrow/></a><a className="av-button av-button-outline" href="/#capabilities" onClick={event => scrollTo(event, 'capabilities')}>了解核心能力<Arrow down/></a></div>
            <div className="av-hero-footnote av-entrance"><Icon name="shield" size={15}/>为独居老人照护与家庭安全而设计</div>
          </div>
        </div>
        <svg className="av-hero-wave" viewBox="0 0 1440 100" preserveAspectRatio="none" aria-hidden="true"><path d="M0 45C260 145 430 5 710 42S1120 125 1440 17"/><path d="M0 50C260 150 430 10 710 47S1120 130 1440 22V100H0Z"/></svg>
        <a className="av-scroll-cue" href="/#capabilities" aria-label="向下了解核心能力" onClick={event => scrollTo(event, 'capabilities')}><Arrow down/></a>
      </section>

      <section id="capabilities" className="av-section av-capabilities" aria-labelledby="capabilities-title">
        <div className="av-container">
          <div className="av-section-top av-reveal"><div><SectionLabel number="01 / CAPABILITIES">核心能力</SectionLabel><h2 id="capabilities-title">重要的异常，<em>及时看见。</em></h2></div><p>把需要留意的时刻，整理成可以回看的证据。</p></div>
          <CapabilityVideos/>
          <p className="av-subtle-note">实景测试素材，点击卡片原位静音播放。跌倒已有模型复核；举手与打火机火焰仅展示原始视频，识别结果待接入。</p>
        </div>
      </section>

      <section id="workflow" className="av-section av-workflow" aria-labelledby="workflow-title">
        <div className="av-container">
          <div className="av-section-top av-reveal"><div><SectionLabel number="02 / WORKFLOW">工作流程</SectionLabel><h2 id="workflow-title">看见异常，<em>了解始末。</em></h2></div><p>让每一次关注，都能找到清晰的来由。</p></div>
          <WorkflowDemo/>
        </div>
      </section>

      <section id="ai-qa" className="av-section av-qa" aria-labelledby="qa-title">
        <div className="av-container av-qa-layout">
          <div className="av-qa-copy av-reveal"><SectionLabel number="03 / ASK WITH AI">AI 问答</SectionLabel><h2 id="qa-title">像家人一样，<br/><em>随时问，随刻答。</em></h2><p>不用逐帧翻看冗长录像。<br/>用自然语言了解家中情况，<br/>让每个回答，都有画面可循。</p><div className="av-example-questions" role="group" aria-label="选择示例问题">{qaExamples.map((example, index) => <button key={example.question} aria-pressed={questionIndex === index} onClick={() => chooseQuestion(index)}><Icon name="chat" size={15}/>{example.question}<Arrow/></button>)}</div></div>
          <div className="av-chat-demo av-reveal">
            <div className="av-chat-heading"><span><span className="av-ai-avatar"><Icon name="spark" size={18}/></span><strong>安居 AI<small>基于既有模型分析结果</small></strong></span><span className="av-chat-demo-label">预设问答演示</span></div>
            <div className="av-chat-content"><div className="av-user-message"><span>{qa.question}</span><span className="av-user-avatar"><Icon name="user" size={17}/></span></div><div className="av-ai-message"><span className="av-ai-avatar"><Icon name="spark" size={17}/></span><div className="av-answer-bubble" aria-live="polite">
              <p>{answer}</p>
              {evidence && evidenceFrame && <>
                <button className="av-qa-evidence" aria-label={`${evidenceExpanded ? '收起' : '展开'}${evidence.title}证据详情`} aria-expanded={evidenceExpanded} aria-controls="av-qa-evidence-detail" onClick={toggleEvidence}>
                  <span className="av-qa-thumb"><img src={evidenceFrame.image} alt="模型实际引用的原始关键帧" loading="lazy" width="720" height="480"/><Icon name="document" size={19}/></span>
                  <span><small>相关证据 · 原片关键帧</small><strong>{evidence.title}</strong><span><Icon name="clock" size={13}/>{evidenceRange(evidence)}</span></span>
                  <span className="av-qa-disclosure" aria-hidden="true"><Arrow down/></span>
                </button>
                <div id="av-qa-evidence-detail" className={`av-inline-evidence${evidenceExpanded ? ' is-expanded' : ' is-closing'}`} role="region" aria-label="示例证据详情" hidden={!evidencePresent} inert={!evidenceExpanded} aria-hidden={!evidenceExpanded}>
                  {evidencePresent && <>
                    <figure><img src={evidenceFrame.image} alt={`模型引用原帧 ${relativeTime(evidenceFrame.ptsMs)}`} width="720" height="480"/><figcaption>真实原片关键帧 · 既有模型复核依据</figcaption></figure>
                    <div className="av-inline-evidence-meta"><span>{qaCase.title}实景测试原片</span><time>相对视频时间 {relativeTime(evidenceFrame.ptsMs)}</time></div>
                    <p>证据区间 {evidenceRange(evidence)}；{reviewLabel(evidence)}，未经人工确认。</p>
                  </>}
                </div>
              </>}
              <p className="av-answer-source"><Icon name="layers" size={12}/>{evidence ? '来源：真实模型导出 · 预先组织的示例文案' : '识别结果待接入 · 当前没有可引用的事件证据'}</p>
            </div></div></div>
            <p className="av-chat-note"><Icon name="chat" size={14}/><span>预设问答演示 · 基于既有模型分析结果，不发起在线推理</span></p>
          </div>
        </div>
      </section>

      <section id="devices" className="av-section av-devices" aria-labelledby="devices-title">
        <div className="av-device-orb" aria-hidden="true"/>
        <div className="av-container av-device-layout">
          <div className="av-device-copy av-reveal"><SectionLabel number="04 / CONNECTION">设备接入</SectionLabel><h2 id="devices-title">从熟悉的设备，<br/><em>开始多一份关怀。</em></h2><p>连接已有视频来源，保留原有生活节奏。<br/>将画面交给 AI，把更多时间留给家人。</p><a className="av-text-link" href={workspaceHref('camera')} onClick={event => enter(event, true)}>前往摄像头工作台<Arrow/></a></div>
          <div className="av-camera-illustration av-reveal"><div className="av-camera-halo"/><img src="/assets/camera.svg" width="340" height="390" loading="lazy" alt="家用云台摄像头外观示意，不代表特定品牌兼容性"/><span>家用摄像头 · 外观示意</span><div className="av-camera-pedestal"/></div>
          <div className="av-device-features av-reveal">{[['plug', '本机摄像头', '通过已配置的本地摄像头服务，查看实时画面。'], ['video', '导入 MP4 录像', '支持上传已有录像，手动启动分析与证据回看。'], ['shield', '按实际配置接入', '摄像头协议与云端分析能力，以后端配置为准。']].map(([icon, title, description]) => <div key={title}><span><Icon name={icon} size={25}/></span><div><h3>{title}</h3><p>{description}</p></div></div>)}</div>
        </div>
      </section>

      <section className="av-final-cta"><div className="av-container av-reveal"><p className="av-eyebrow">ANJU VISION · 让关心有据可查</p><h2>距离可以很远，<em>关心可以更近。</em></h2><p>让 AI 看见生活中的线索，让家人更便捷地了解居家情况。</p><a className="av-button" href={workspaceHref('camera')} onClick={event => enter(event, true)}><Icon name="monitor" size={20}/>进入工作台<Arrow/></a>{demos.length > 0 && <div className="av-real-demos"><span>已有保存案例</span>{demos.slice(0, 3).map(demo => <button key={demo.id} onClick={() => onOpenDemo?.(demo.id)}>{demo.name.replace('.mp4', '').slice(0, 26)}<Arrow/></button>)}</div>}</div></section>
    </main>
    <footer className="av-footer"><div className="av-container"><Brand compact/><span>独居老人照护与家庭安全 · AI 视频分析平台</span><a href="/#home" onClick={event => scrollTo(event, 'home')}>回到顶部<Arrow/></a></div></footer>
  </div>;
}
