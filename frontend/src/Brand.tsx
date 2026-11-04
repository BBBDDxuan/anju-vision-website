import { Icon } from './ui';

export function Brand({ compact = false }: { compact?: boolean }) {
  return <span className={`av-brand${compact ? ' av-brand-compact' : ''}`}>
    <svg viewBox="0 0 48 48" fill="none" aria-hidden="true"><path d="m5 22 17-15a3 3 0 0 1 4 0l17 15v18a2 2 0 0 1-3 1l-13-9a5 5 0 0 0-6 0L8 41a2 2 0 0 1-3-1Z" stroke="currentColor" strokeWidth="2.5" strokeLinejoin="round"/><circle cx="33" cy="24" r="3" fill="currentColor"/></svg>
    <span><strong>安居视界</strong><small>ANJU VISION</small></span>
  </span>;
}

export function SectionLabel({ children, number }: { children: string; number: string }) {
  return <p className="av-section-label"><span/>{children}<small>{number}</small></p>;
}

export function Arrow({ down = false }: { down?: boolean }) {
  return <span className={down ? 'av-arrow-down' : 'av-arrow'}><Icon name="arrow" size={18}/></span>;
}
