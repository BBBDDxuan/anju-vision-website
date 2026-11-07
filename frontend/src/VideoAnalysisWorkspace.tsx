import { useRef, type KeyboardEvent } from 'react';
import { Icon } from './ui';
import type { InspectorTab } from './navigation';

const items = [
  { id: 'events', title: '事件提醒', icon: 'clock' },
  { id: 'qa', title: 'AI 问答', icon: 'chat' },
  { id: 'tools', title: '目标追踪', icon: 'search' },
] as const;

export function InspectorTabs({ active, onChange }: { active: InspectorTab; onChange: (tab: InspectorTab) => void }) {
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (event: KeyboardEvent, index: number) => {
    const next = event.key === 'ArrowRight' ? (index + 1) % 3 : event.key === 'ArrowLeft' ? (index + 2) % 3 : event.key === 'Home' ? 0 : event.key === 'End' ? 2 : -1;
    if (next < 0) return;
    event.preventDefault(); onChange(items[next].id); tabs.current[next]?.focus();
  };
  return <div className="workspace-inspector-tabs" role="tablist" aria-label="当前视频功能">{items.map((item, i) => <button key={item.id} ref={node => { tabs.current[i] = node; }} role="tab" id={`inspector-tab-${item.id}`} aria-controls={`inspector-panel-${item.id}`} aria-selected={active === item.id} tabIndex={active === item.id ? 0 : -1} onClick={() => onChange(item.id)} onKeyDown={event => onKey(event, i)}><Icon name={item.icon} size={16}/>{item.title}</button>)}</div>;
}
