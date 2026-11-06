import { useContext, useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { AccountActionsContext, AccountContext } from './AccountGate';
import { Icon } from './ui';

export function avatarText(name: string): string {
  const trimmed = name.trim();
  const letters = typeof Intl.Segmenter === 'function'
    ? Array.from(new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(trimmed), part => part.segment)
    : Array.from(trimmed);
  return letters.slice(0, 2).join('');
}

export function AccountAvatar({ fallback }: { fallback?: ReactNode }) {
  const user = useContext(AccountContext);
  const actions = useContext(AccountActionsContext);
  const [open, setOpen] = useState(false);
  const wrapper = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    function outside(event: PointerEvent) {
      if (event.target instanceof Node && !wrapper.current?.contains(event.target)) setOpen(false);
    }
    function escape(event: KeyboardEvent) {
      if (event.key !== 'Escape') return;
      setOpen(false);
      trigger.current?.focus();
    }
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);
  if (!user || !actions) return fallback ?? null;
  const initials = avatarText(user.display_name);
  const role = user.role === 'admin' ? '管理员' : '家庭成员';
  return <div className="account-avatar" ref={wrapper}>
    <button className="account-avatar-trigger" type="button" ref={trigger}
      aria-label="个人信息" aria-expanded={open} aria-controls={open ? id : undefined}
      onClick={() => setOpen(value => !value)}>
      {initials || <Icon name="user" size={18} />}
    </button>
    {open && <section className="account-avatar-panel" id={id} aria-label="当前账号">
      <div className="account-avatar-heading">
        <span className="account-avatar-badge" aria-hidden="true">{initials || <Icon name="user" size={18} />}</span>
        <div><strong>{user.display_name}</strong><small>{user.household_name}</small></div>
      </div>
      <dl className="account-avatar-details">
        <dt>用户名</dt><dd>{user.username}</dd>
        <dt>家庭权限</dt><dd>{role}</dd>
      </dl>
      <div className="account-avatar-actions">
        <button type="button" onClick={() => {
          setOpen(false);
          actions.openManagement();
        }}>{user.role === 'admin' ? '家庭管理' : '注册家庭成员'}</button>
        <button type="button" disabled={actions.busy} onClick={() => void actions.logout()}>退出登录</button>
      </div>
      {actions.message && <p role="alert">{actions.message}</p>}
    </section>}
  </div>;
}
