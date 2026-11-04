import { useEffect, useRef, useState, type ReactNode, type MouseEvent } from 'react';
import { durationLabel, Icon } from './ui';
import { Brand } from './Brand';
import type { IntroductionPage } from './introduction';
import { workspaceHref, type View } from './navigation';

type Video = { id: string; name: string; duration_ms: number; active_run?: string | null; read_only_demo?: boolean };
type Props = { children: ReactNode; videos: Video[]; selected: string; onSelect: (id: string) => void; ready: boolean; loading?: boolean; serviceError?: boolean; localCamera?: boolean; model: string; view: View; introductionPage?: IntroductionPage; onIntroductionNavigate: (page: IntroductionPage) => void; onNavigate: (view: View) => void; onDelete: (video: Video) => void; busyVideoId?: string };

export function Shell({ children, videos, selected, onSelect, ready, loading, serviceError, model, view, onNavigate, onDelete, busyVideoId }: Props) {
  const [open, setOpen] = useState(false);
  const [compact, setCompact] = useState(() => typeof window !== 'undefined' && window.matchMedia('(max-width: 1024px)').matches);
  const [search, setSearch] = useState('');
  const help = useRef<HTMLDialogElement>(null);
  const sidebar = useRef<HTMLElement>(null);
  const items = videos.filter(video => video.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()));
  const serviceState = serviceError ? 'error' : loading ? 'loading' : ready ? 'ready' : 'unconfigured';
  const serviceLabel = serviceError ? '服务连接异常' : loading ? '正在连接服务' : ready ? '录像分析可用' : '云端分析待配置';
  const navigation = [
    { view: 'camera' as const, icon: 'video', label: '实时摄像头' },
    { view: 'videos' as const, icon: 'upload', label: 'AI 视频分析' },
  ];
  const activeLabel = view === 'camera' ? '实时摄像头' : 'AI 视频分析';
  const navigateLink = (event: MouseEvent<HTMLAnchorElement>, next: View) => {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); onNavigate(next); setOpen(false);
  };
  useEffect(() => {
    const media = matchMedia('(max-width: 1024px)');
    const update = () => { setCompact(media.matches); if (!media.matches) setOpen(false); };
    update(); media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    sidebar.current?.querySelector<HTMLButtonElement>('.drawer-close')?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); setOpen(false); }
      if (event.key !== 'Tab') return;
      const focusable = Array.from(sidebar.current?.querySelectorAll<HTMLElement>('a[href],button:not(:disabled),input:not(:disabled),summary') || []).filter(element => element.getClientRects().length > 0);
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => { document.body.style.overflow = previousOverflow; document.removeEventListener('keydown', onKeyDown); if (previousFocus?.getClientRects().length) previousFocus.focus(); };
  }, [open]);

  if (view === 'home') return <>{children}</>;
  return <div className={`app-shell av-workspace-shell ${view === 'camera' ? 'camera-shell' : 'unified-shell'}`}>
    <a className="skip-link" href="#main-content">跳到主要内容</a>
    <button className={`drawer-backdrop ${open ? 'is-open' : ''}`} aria-label="关闭导航" aria-hidden={!open} inert={!open} tabIndex={-1} onClick={() => setOpen(false)}/>
    <aside ref={sidebar} id="library-navigation" className={`sidebar ${open ? 'is-open' : ''}`} inert={compact && !open} aria-label="功能导航" role={open ? 'dialog' : undefined} aria-modal={open ? true : undefined}>
      <div className="sidebar-top"><a href="/" className="av-workspace-brand" aria-label="安居视界，返回官网" onClick={event => navigateLink(event, 'home')}><Brand/></a><button className="icon-button drawer-close" aria-label="关闭导航" onClick={() => setOpen(false)}><Icon name="close" size={18}/></button></div>
      <nav className="sidebar-section" aria-label="主要导航"><span className="sidebar-caption">家庭安全工作台</span>
        {navigation.map(item => <a key={item.label} className={`workspace-link ${view === item.view ? 'is-active' : ''}`} href={workspaceHref(item.view)} aria-current={view === item.view ? 'page' : undefined} onClick={event => navigateLink(event, item.view)}><Icon name={item.icon} size={19}/>{item.label}{view === item.view && <i/>}</a>)}
      </nav>
      <div className="workspace-sidebar-middle">{view === 'videos' && <details className="av-recording-library" open><summary><Icon name="layers" size={15}/>我的录像<span>{videos.length}</span></summary><label className="library-search"><Icon name="search" size={15}/><input aria-label="搜索录像" name="library-search" autoComplete="off" value={search} onChange={event => setSearch(event.target.value)} placeholder="查找居家录像…"/></label><div className="library-list">{items.length ? items.map(video => <div className="library-row" key={video.id}><button className={`library-item ${selected === video.id ? 'selected' : ''}`} aria-current={selected === video.id ? 'true' : undefined} onClick={() => { onSelect(video.id); setOpen(false); }}><span className="library-icon"><Icon name="video" size={16}/></span><span><strong title={video.name}>{video.name}</strong><small>{durationLabel(video.duration_ms)}<i/>{video.active_run ? '有分析记录' : '待分析'}</small></span></button><button className="library-delete" title="删除录像" aria-label={`删除录像 ${video.name}`} disabled={busyVideoId === video.id || video.read_only_demo} onClick={() => onDelete(video)}><Icon name="trash" size={14}/></button></div>) : <p className="library-empty">{videos.length ? '没有匹配的录像' : '导入第一段录像，开始了解居家情况。'}</p>}</div></details>}</div>
      <div className="sidebar-bottom"><div className="av-sidebar-note"><Icon name="shield" size={23}/><p>每一份关心，都有迹可循。<small>视频证据 · 候选复核 · 原片回看</small></p></div><a className="av-back-home" href="/" onClick={event => navigateLink(event, 'home')}><Icon name="home" size={17}/>返回产品官网<Icon name="arrow" size={16}/></a><button className="help-button" onClick={() => { setOpen(false); requestAnimationFrame(() => help.current?.showModal()); }}><Icon name="help" size={16}/>使用说明<Icon name="arrow" size={14}/></button></div>
    </aside>
    <div className="app-body" inert={open}><header className="topbar"><div className="topbar-left"><button className="icon-button mobile-library" aria-label="打开导航" aria-expanded={open} aria-controls="library-navigation" onClick={() => setOpen(true)}><Icon name="menu"/></button><span className="breadcrumb">我的工作台<span>/</span><strong>{activeLabel}</strong></span></div><div className="av-workspace-top-right"><span className="model-status" data-state={serviceState} role="status"><span className={`status-dot ${serviceState}`} aria-hidden="true"/>{serviceLabel}{model && <code title={model}>{model}</code>}</span><span className="av-workspace-avatar" aria-label="家庭看护空间"><Icon name="user" size={18}/></span></div></header>{children}</div>
    <div inert={open}><nav className="mobile-nav" aria-label="手机工作台导航"><button aria-expanded={open} aria-controls="library-navigation" onClick={() => setOpen(true)}><Icon name="menu" size={19}/>导航</button>{navigation.map(item => <a key={item.label} href={workspaceHref(item.view)} aria-current={view === item.view ? 'page' : undefined} onClick={event => navigateLink(event, item.view)}><Icon name={item.icon} size={19}/>{item.label}</a>)}</nav></div>
    <dialog ref={help} className="help-dialog" aria-labelledby="help-title"><div className="section-heading"><h2 id="help-title">从一段录像，到可回看的证据</h2><button className="icon-button" aria-label="关闭使用说明" onClick={() => help.current?.close()}><Icon name="close"/></button></div><ol><li>连接已配置的本机摄像头，或导入 MP4 录像。</li><li>选择安全分析或物品追踪。任务支持暂停与继续。</li><li>从事件时间线或问答中的时间引用，回到原视频和关键帧。</li><li>结合候选复核状态判断线索。演示画面与真实分析结果分开显示。</li></ol></dialog>
  </div>;
}
