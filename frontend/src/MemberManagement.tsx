import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Icon } from './ui';
import { MemberFacePanel } from './MemberFacePanel';
import { MemberApiError, memberRequest, memberTitle, faceStatusMessage, type Member, type FaceStatus } from './memberApi';
import './members.css';

function Avatar({ member }: { member: Member }) {
  return <span className="member-avatar member-initial" aria-hidden="true" style={{ background: /^#[0-9a-f]{6}$/i.test(member.label_color || '') ? member.label_color : '#3f594b', color: '#fff' }}>{member.label_letter || memberTitle(member).slice(0, 1)}</span>;
}

export function MemberManagement({ cloud = false, ready = true }: { cloud?: boolean; ready?: boolean }) {
  const [members, setMembers] = useState<Member[]>([]);
  const [draft, setDraft] = useState<Member | null>(null);
  const [status, setStatus] = useState<FaceStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [conflict, setConflict] = useState(false);
  const operation = useRef(false);
  const lifecycle = useRef<AbortController | null>(null);
  const errorMessage = (e: unknown) => e instanceof Error ? e.message : '操作失败，请稍后重试。';

  async function refresh(signal?: AbortSignal, selectedId?: string) {
    setLoading(true); setError('');
    try {
      const [profiles, faceStatus] = await Promise.all([
        memberRequest<{ members: Member[] }>('/api/household/members', { signal }),
        memberRequest<FaceStatus>('/api/faces/status', { signal }).catch(() => null),
      ]);
      if (signal?.aborted) return;
      setMembers(profiles.members); setStatus(faceStatus);
      setDraft(profiles.members.find(m => m.id === selectedId) || profiles.members[0] || null);
      setConflict(false);
    } catch (e) { if (!signal?.aborted) setError(errorMessage(e)); }
    finally { if (!signal?.aborted) setLoading(false); }
  }
  useEffect(() => {
    const controller = new AbortController(); lifecycle.current = controller;
    if (ready && !cloud) void refresh(controller.signal);
    else setLoading(!ready);
    return () => controller.abort();
  }, [cloud, ready]);

  function edit(member: Member) { setDraft({ ...member }); setError(''); setNotice(''); setConflict(false); }
  function add() {
    edit({ id: `m-${crypto.randomUUID()}`, number: Math.max(0, ...members.map(m => m.number)) + 1,
      name: '', relationship: '', notes: '', bindings: [], revision: 0, face_sample_count: 0,
      label_color: '#3f594b', label_letter: '' });
  }
  function accept(member: Member) {
    setMembers(current => [...current.filter(m => m.id !== member.id), member].sort((a, b) => a.number - b.number));
    setDraft(member);
  }
  async function save(event: FormEvent) {
    event.preventDefault(); if (!draft || operation.current) return;
    operation.current = true; setBusy(true); setError(''); setNotice('');
    try {
      // Keep API writes explicit, including bindings owned by other clients.
      const body = { id: draft.id, number: draft.number, name: draft.name, relationship: draft.relationship,
        notes: draft.notes, bindings: draft.bindings, revision: draft.revision,
        label_color: draft.label_color, label_letter: draft.label_letter };
      const saved = (await memberRequest<{ member: Member }>(`/api/household/members/${draft.id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: lifecycle.current?.signal,
      })).member;
      accept(saved); setConflict(false); setNotice('成员档案已保存，可在下方录入人脸。');
    } catch (e) {
      if (!lifecycle.current?.signal.aborted) { setError(errorMessage(e)); setConflict(e instanceof MemberApiError && e.code === 'HOUSEHOLD_REVISION_CONFLICT'); }
    } finally { operation.current = false; setBusy(false); }
  }
  async function remove() {
    if (!draft || operation.current || !window.confirm(`删除“${memberTitle(draft)}”的档案及全部人脸资料？安全事件会保留。`)) return;
    operation.current = true; setBusy(true); setError(''); setNotice('');
    try {
      await memberRequest(`/api/household/members/${draft.id}?revision=${draft.revision}`, { method: 'DELETE', signal: lifecycle.current?.signal });
      setMembers(current => current.filter(m => m.id !== draft.id)); setDraft(null); setNotice('成员及人脸资料已删除。');
    } catch (e) { setError(errorMessage(e)); setConflict(e instanceof MemberApiError && e.code === 'HOUSEHOLD_REVISION_CONFLICT'); }
    finally { operation.current = false; setBusy(false); }
  }
  const persisted = draft && members.find(member => member.id === draft.id);
  const dirty = !!draft && (!persisted || JSON.stringify(draft) !== JSON.stringify(persisted));
  const select = (member: Member | null) => {
    if (dirty && !window.confirm('尚有未保存的档案修改，放弃这些修改？')) return;
    if (member) edit(member); else add();
  };
  const memberActions = <div className="member-actions member-management-actions"><button type="button" disabled={busy || loading || !ready} onClick={() => { if (dirty && !window.confirm('刷新将丢弃未保存的修改，继续？')) return; setNotice(''); void refresh(lifecycle.current?.signal, draft?.id); }}>刷新列表</button>{members.length > 0 && <button type="button" className="primary" disabled={busy || loading || !ready || members.length >= 32} onClick={() => select(null)}>添加成员</button>}</div>;
  return <section className="members-page" aria-label="家庭成员" aria-busy={loading || busy}>
    {cloud ? <p className="members-notice" role="status">请在电脑本地启动服务后管理家庭档案和人脸资料。</p> : <>
      {!loading && faceStatusMessage(status) && <div className="member-service" role="status" data-ready={false}><Icon name="shield" size={19}/><strong>{faceStatusMessage(status)}</strong></div>}
      {error && <p className="members-error" role="alert">{error}</p>}{notice && <p className="members-notice" role="status">{notice}</p>}
      {conflict && <div className="members-conflict"><p>当前编辑内容已保留。载入最新档案会丢弃这些编辑。</p><button disabled={busy} onClick={() => { if (window.confirm('丢弃当前编辑，载入服务端最新档案？')) void refresh(lifecycle.current?.signal, draft?.id); }}>载入最新档案</button></div>}
      {!loading && !error && members.length === 0 && !draft ? <div className="member-onboarding"><div className="member-onboarding-actions">{memberActions}</div><span className="onboarding-icon"><Icon name="user" size={32}/></span><h2>建立你的家庭成员档案</h2><p>添加家庭成员，帮助系统在监控画面中辅助识别家人。</p><button className="primary" disabled={busy || !ready} onClick={add}>+ 添加家庭成员</button><ol className="product-steps"><li><span>01</span><strong>填写资料</strong><p>填写姓名与家庭关系。</p></li><li><span>02</span><strong>录入照片</strong><p>上传不同角度的家庭成员照片。</p></li><li><span>03</span><strong>开启识别</strong><p>开启家庭成员辅助识别。</p></li></ol></div> : <div className={`members-layout${members.length === 0 ? ' members-layout-single' : ''}`}>{members.length > 0 && <aside className="members-list" aria-label="家庭成员列表"><div className="members-list-heading"><h2>成员列表 <span>{members.length}/32</span></h2>{memberActions}</div>
        {loading ? <p role="status">正在读取家庭档案…</p> : members.length === 0 ? <p>还没有成员，点击“添加成员”建立第一份档案。</p> : members.map(member => <button key={member.id} className={`member-row ${draft?.id === member.id ? 'is-selected' : ''}`} aria-pressed={draft?.id === member.id} disabled={busy} onClick={() => select(member)}><Avatar member={member}/><span><strong>{memberTitle(member)}</strong><small>{member.relationship || '家人'} · {member.face_sample_count > 0 ? `已录入 ${member.face_sample_count} 张` : '待录入人脸'}</small></span><Icon name="arrow" size={16}/></button>)}
      </aside>}{draft ? <div className="member-detail"><form className="member-editor" onSubmit={event => void save(event)} aria-label="成员档案编辑"><div className="member-editor-heading"><h2>{draft.revision === 0 ? '添加成员' : memberTitle(draft)}</h2>{members.length === 0 && memberActions}</div><fieldset disabled={busy || loading}>
        <div className="member-avatar-editor"><Avatar member={draft}/><p>姓名和关系会显示在家人的安全提醒中。</p></div>
        <label>姓名<input aria-label="姓名" value={draft.name} maxLength={80} placeholder={`留空显示“成员${draft.number}”`} onChange={e => setDraft({ ...draft, name: e.target.value })}/></label>
        <div className="member-form-row"><label>关系<input aria-label="关系" value={draft.relationship} maxLength={80} placeholder="如：父亲、母亲" onChange={e => setDraft({ ...draft, relationship: e.target.value })}/></label><label>标签颜色<input type="color" aria-label="标签颜色" value={draft.label_color || '#3f594b'} onChange={e => setDraft({ ...draft, label_color: e.target.value })}/></label><label>简称<input aria-label="简称" value={draft.label_letter || ''} maxLength={2} placeholder="姓名首字" onChange={e => setDraft({ ...draft, label_letter: e.target.value })}/></label></div>
        <label>备注<textarea aria-label="备注" value={draft.notes} maxLength={2000} rows={3} onChange={e => setDraft({ ...draft, notes: e.target.value })}/></label>
        <div className="member-actions"><button className="primary" type="submit" disabled={conflict || !dirty}>{busy ? '正在保存…' : '保存档案'}</button>{draft.revision > 0 && <button className="danger" type="button" onClick={() => void remove()}>删除成员</button>}</div>
      </fieldset></form>
        {persisted ? <MemberFacePanel key={persisted.id} member={persisted} status={status} disabled={dirty || busy || loading || conflict} onBusy={setBusy} onUpdated={accept}/> : <div className="member-face-panel"><h2>人脸资料</h2><p>先保存档案，再为这位成员录入人脸。</p></div>}
      </div> : <div className="member-editor member-editor-empty" role="status"><Icon name="user" size={32}/><h2>{loading ? '正在读取家庭档案…' : error ? '暂时无法读取家庭档案' : '选择一位家人'}</h2><p>{error ? '可点击刷新列表重试。' : loading ? '请稍候。' : '从成员列表中查看资料和照片。'}</p>{members.length === 0 && memberActions}</div>}</div>}
    </>}
  </section>;
}
