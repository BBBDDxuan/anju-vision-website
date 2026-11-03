import { useEffect, useRef, useState, type ReactNode, type MouseEvent } from 'react';
import { durationLabel, Icon } from './ui';
import { introductionPages, introductionHref, type IntroductionPage } from './introduction';
import { workspaceHref, type View } from './navigation';

type Video = { id: string; name: string; duration_ms: number; active_run?: string | null; read_only_demo?: boolean };
type Props = { children: ReactNode; videos: Video[]; selected: string; onSelect: (id: string) => void; ready: boolean; loading?: boolean; serviceError?: boolean; localCamera?: boolean; model: string; view: View; introductionPage?: IntroductionPage; onIntroductionNavigate: (page: IntroductionPage) => void; onNavigate: (view: View) => void; onDelete: (video: Video) => void; busyVideoId?: string };

export function Shell({ children, videos, selected, onSelect, ready, loading, serviceError, localCamera = true, model, view, introductionPage = 'overview', onIntroductionNavigate, onNavigate, onDelete, busyVideoId }: Props) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const help = useRef<HTMLDialogElement>(null);
  const sidebar = useRef<HTMLElement>(null);
  const items = videos.filter(v => v.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()));
  const navigate = (next: View) => { onNavigate(next); setOpen(false); };
  const navigateLink = (event: MouseEvent<HTMLAnchorElement>, next: View) => {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); navigate(next);
  };
  const introductionLink = (event: MouseEvent<HTMLAnchorElement>, next: IntroductionPage) => {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); onIntroductionNavigate(next); setOpen(false);
  };
  const serviceState = serviceError ? 'error' : loading ? 'loading' : ready ? 'ready' : 'unconfigured';
  const serviceLabel = serviceError ? '服务状态不可用' : loading ? '读取服务状态…' : ready ? '录像分析可用' : '录像分析待配置';

  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    sidebar.current?.querySelector<HTMLButtonElement>('.drawer-close')?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); setOpen(false); }
      if (event.key !== 'Tab') return;
      const focusable = Array.from(sidebar.current?.querySelectorAll<HTMLElement>('a[href],button:not(:disabled),input:not(:disabled)') || [])
        .filter(element => element.getClientRects().length > 0);
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    const onResize = () => { if (window.innerWidth > 1024) setOpen(false); };
    document.addEventListener('keydown', onKeyDown);
    window.addEventListener('resize', onResize);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('resize', onResize);
      previousFocus?.focus();
    };
  }, [open]);

  return <div className={`app-shell ${view === 'home' ? 'introduction-shell' : view === 'camera' ? 'camera-shell' : ''}`}>
    <a className="skip-link" href="#main-content">跳到主要内容</a>
    {open && <button className="drawer-backdrop" aria-label="点击空白区域关闭导航" tabIndex={-1} onClick={() => setOpen(false)} />}
    <aside ref={sidebar} id="library-navigation" className={`sidebar ${open ? 'is-open' : ''}`} aria-label="功能导航" role={open ? 'dialog' : undefined} aria-modal={open ? true : undefined}>
      <div className="sidebar-top">
        <a className="brand" href="/" onClick={event => navigateLink(event, 'home')}><span className="brand-mark"><Icon name="home" size={22} /></span><span>安居视界<small>独居关怀与家庭安全</small></span></a>
        <button className="icon-button drawer-close" aria-label="关闭导航" onClick={() => setOpen(false)}><Icon name="close" size={18} /></button>
      </div>
      <nav className="sidebar-section" aria-label="主要导航">
        <span className="sidebar-caption">我的空间</span>
        <div className="intro-nav-group">
          <a className={`workspace-link ${view === 'home' ? 'is-active' : ''}`} href="/" aria-current={view === 'home' ? 'page' : undefined} onClick={event => navigateLink(event, 'home')}><Icon name="home"/>产品介绍</a>
          {view === 'home' && <nav className="intro-links" aria-label="介绍页导航">{introductionPages.map(item => <a key={item.id} href={introductionHref(item.id)} aria-current={introductionPage === item.id ? 'page' : undefined} onClick={event => introductionLink(event, item.id)}>{item.label}</a>)}</nav>}
        </div>
        <div className="camera-nav-group">
          <a className={`workspace-link ${view === 'camera' ? 'is-active' : ''}`} href={workspaceHref('camera')} aria-current={view === 'camera' ? 'page' : undefined} onClick={event => navigateLink(event, 'camera')}><Icon name="video" />实时摄像头</a>
          {view === 'camera' && <details className="sidebar-subsection camera-subsection"><summary>实时关注家中日常</summary><div className="sidebar-subcontent"><p>{localCamera ? '查看摄像头画面、录像状态与本次会话事件。需要回看已有录像时，请选择“导入视频”。' : '实时摄像头需要在本机环境中使用。当前可选择“导入视频”分析录像。'}</p></div></details>}
        </div>
        <div className="video-nav-group">
          <a className={`workspace-link ${view === 'videos' ? 'is-active' : ''}`} href={workspaceHref('videos')} aria-current={view === 'videos' ? 'page' : undefined} onClick={event => navigateLink(event, 'videos')}><Icon name="upload" />导入视频</a>
          {view === 'videos' && <details className="sidebar-subsection library-subsection"><summary>录像库<span className="library-count">{videos.length}</span></summary><div className="sidebar-subcontent">
        <label className="library-search"><Icon name="search" size={16} /><input aria-label="搜索录像" name="library-search" autoComplete="off" value={search} onChange={e => setSearch(e.target.value)} placeholder="查找录像…" /></label>
        <div className="library-list">{items.length ? items.map(v => <div className="library-row" key={v.id}>
          <button className={`library-item ${selected === v.id ? 'selected' : ''}`} aria-current={selected === v.id ? 'true' : undefined} onClick={() => { onSelect(v.id); setOpen(false); }}><span className="library-icon"><Icon name="video" size={18} /></span><span><strong title={v.name}>{v.name}</strong><small>{durationLabel(v.duration_ms)}<i />{v.active_run ? '有分析记录' : '待分析'}</small></span></button>
          <button className="library-delete" title="删除录像" aria-label={`删除录像 ${v.name}`} disabled={busyVideoId === v.id || v.read_only_demo} onClick={() => onDelete(v)}><Icon name="trash" size={15}/></button>
        </div>) : <p className="library-empty">{videos.length ? '没有匹配的录像，试试其他名称。' : '还没有居家录像。\n导入第一段视频，在这里回看和管理。'}</p>}</div>
          </div></details>}
        </div>
      </nav>
      <div className="sidebar-bottom"><div className="future-note"><Icon name="shield" size={18} /><div><strong>记录保存在服务端</strong><p>分析时仅发送采样画面。</p></div></div><button className="help-button" onClick={() => { setOpen(false); requestAnimationFrame(() => help.current?.showModal()); }}><Icon name="help" size={17} />使用说明<Icon name="arrow" size={16} /></button></div>
    </aside>
    <div className="app-body" inert={open}>
      <header className="topbar"><div className="topbar-left"><button className="icon-button mobile-library" aria-label={view === 'videos' ? '打开录像库' : '打开导航'} aria-expanded={open} aria-controls="library-navigation" onClick={() => setOpen(true)}><Icon name="menu" /></button><span className="breadcrumb">我的空间 <span>/</span> <strong>{view === 'videos' ? '导入视频' : view === 'camera' ? '实时摄像头' : '产品介绍'}</strong></span></div>
        {view === 'videos' ? <div className="model-status" data-state={serviceState} role="status"><span className={`status-dot ${serviceState}`} aria-hidden="true"/><span>{serviceLabel}</span>{model && <code title={model} translate="no">{model}</code>}</div> : <span className="landing-top-note"><Icon name="shield" size={15}/>{view === 'camera' ? '本机实时看护' : '独居关怀与家庭安全'}</span>}
      </header>
      {children}
    </div>
    <div inert={open}>{view !== 'home' ? <nav className="mobile-nav" aria-label="手机工作台导航"><button aria-expanded={open} aria-controls="library-navigation" onClick={() => setOpen(true)}><Icon name="menu" size={19} />导航</button><a href={workspaceHref('camera')} aria-current={view === 'camera' ? 'page' : undefined} onClick={event => navigateLink(event, 'camera')}><Icon name="video" size={19} />实时摄像头</a><a href={workspaceHref('videos')} aria-current={view === 'videos' ? 'page' : undefined} onClick={event => navigateLink(event, 'videos')}><Icon name="upload" size={19} />导入视频</a>{view === 'videos' && <><a href="#watch"><Icon name="clock" size={19} />回放</a><a href="#ask"><Icon name="chat" size={19} />问答</a></>}</nav> : <nav className="mobile-nav intro-mobile-nav" aria-label="手机介绍页导航">{introductionPages.map(item => <a key={item.id} href={introductionHref(item.id)} aria-current={introductionPage === item.id ? 'page' : undefined} onClick={event => introductionLink(event, item.id)}><Icon name={item.icon} size={19}/>{item.label.replace('产品','').replace('核心','').replace('使用','')}</a>)}<button onClick={() => setOpen(true)}><Icon name="menu" size={19}/>导航</button></nav>}</div>
    <dialog ref={help} className="help-dialog" aria-labelledby="help-title"><div className="section-heading"><h2 id="help-title">居家视频监测与证据回溯</h2><button className="icon-button" aria-label="关闭使用说明" onClick={() => help.current?.close()}><Icon name="close" /></button></div><ol><li>上传单机位 MP4 录像，最长24小时。</li><li>选择安全分析或物品追踪。任务支持暂停与继续，保留已有进度。</li><li>通过时间线找事件，或用自然语言提问，点击时间引用回看原视频与关键帧。</li><li>绿色表示较高可信，黄色表示中等可信；点击线索查看证据。</li></ol></dialog>
  </div>;
}
