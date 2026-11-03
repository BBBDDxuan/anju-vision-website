import { useEffect, useRef } from 'react';
import { Icon } from './ui';

type Props = { name: string; deleting: boolean; error: string; onCancel: () => void; onConfirm: () => void };

export function DeleteVideoDialog({ name, deleting, error, onCancel, onConfirm }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  return <dialog ref={dialog} className="delete-dialog" aria-labelledby="delete-title" onCancel={event => { event.preventDefault(); if (!deleting) onCancel(); }}>
    <span className="delete-dialog-icon"><Icon name="trash" size={25}/></span><h2 id="delete-title">删除这段录像？</h2><p className="delete-name">{name}</p>
    <p>这将删除该录像及其所有分析结果、证据帧和相关历史记录，无法撤销。</p><p className="muted">其他录像仍在使用的共享原片会保留。已另存的原片、备份及已发生的 API 费用记录不会删除。</p>
    {error && <p className="error" role="alert">{error}</p>}<div className="delete-actions"><button type="button" autoFocus disabled={deleting} onClick={onCancel}>取消</button><button type="button" className="danger-button" disabled={deleting} onClick={onConfirm}>{deleting ? '正在删除…' : '确认删除'}</button></div>
  </dialog>;
}
