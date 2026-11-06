import { useEffect, useRef, useState } from 'react';
import { faceFileError, faceIdentityLabel, memberRequest, memberTitle, type FaceStatus, type Member, type Recognition } from './memberApi';

type Props = { member: Member; status: FaceStatus | null; disabled: boolean; onBusy: (busy: boolean) => void; onUpdated: (member: Member) => void };
export function MemberFacePanel({ member, status, disabled, onBusy, onUpdated }: Props) {
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [testFile, setTestFile] = useState<File | null>(null);
  const [preview, setPreview] = useState('');
  const [result, setResult] = useState<Recognition | null>(null);
  const active = useRef<AbortController | null>(null);
  const pending = useRef(false);
  const uploadInput = useRef<HTMLInputElement>(null);
  const testInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const controller = new AbortController(); active.current = controller;
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!testFile) { setPreview(''); return; }
    const url = URL.createObjectURL(testFile); setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [testFile]);
  const ready = status?.enabled === true && status.ready;
  const limit = status?.max_samples_per_member || 10;
  const locked = disabled || busy;
  const begin = () => { pending.current = true; setBusy(true); onBusy(true); setError(''); setNotice(''); setResult(null); };
  const end = () => { pending.current = false; if (!active.current?.signal.aborted) { setBusy(false); onBusy(false); } };
  const showError = (e: unknown) => { if (!active.current?.signal.aborted) setError(e instanceof Error ? e.message : '操作失败，请重试。'); };
  async function syncProfile() {
    const { members } = await memberRequest<{ members: Member[] }>('/api/household/members', { signal: active.current?.signal });
    const updated = members.find(m => m.id === member.id);
    if (!updated) throw new Error('成员已被删除，请刷新列表。');
    if (!active.current?.signal.aborted) onUpdated(updated);
  }
  async function enroll(files: File[]) {
    if (!files.length || locked || pending.current || !consent || !ready) return;
    const invalid = files.map(file => faceFileError(file, status?.max_image_bytes)).find(Boolean);
    if (invalid) { setError(invalid); return; }
    if (files.length + member.face_sample_count > limit) { setError(`最多录入 ${limit} 张，当前还可以录入 ${Math.max(0, limit - member.face_sample_count)} 张。`); return; }
    begin(); let added = 0;
    try {
      for (const file of files) {
        setNotice(`正在录入 ${added + 1}/${files.length}：${file.name}`);
        const params = new URLSearchParams({ name: memberTitle(member), consent: 'true' });
        await memberRequest(`/api/faces/members/${member.id}/samples?${params}`, {
          method: 'POST', headers: { 'Content-Type': file.type }, body: file, signal: active.current?.signal,
        });
        added++;
      }
    } catch (e) { showError(e); }
    finally {
      if (added) {
        setNotice(`已成功录入 ${added}/${files.length} 张照片的特征。`);
        try { await syncProfile(); } catch { showError(new Error('已有照片成功录入，但档案刷新失败，请刷新列表查看最新数量。')); }
      } else setNotice('');
      end();
    }
  }
  async function clearSamples() {
    if (locked || pending.current || !window.confirm(`清除“${memberTitle(member)}”的全部人脸特征？成员档案和安全事件会保留，旧身份关联会撤销。`)) return;
    begin();
    try {
      await memberRequest(`/api/faces/members/${member.id}/samples`, { method: 'DELETE', signal: active.current?.signal });
      setNotice('人脸资料已清除，旧告警中的人脸身份关联已撤销。'); await syncProfile();
    } catch (e) { showError(e); }
    finally { end(); }
  }
  async function recognize(file?: File) {
    if (!file || locked || pending.current || !ready) return;
    const invalid = faceFileError(file, status?.max_image_bytes);
    if (invalid) { setError(invalid); return; }
    begin(); setTestFile(file);
    try {
      const next = await memberRequest<Recognition>('/api/faces/recognize', {
        method: 'POST', headers: { 'Content-Type': file.type }, body: file, signal: active.current?.signal,
      });
      if (!active.current?.signal.aborted) setResult(next);
    } catch (e) { showError(e); }
    finally { end(); }
  }
  return <section className="member-face-panel" aria-label="人脸资料" aria-busy={busy}>
    <div className="member-face-heading"><h2>人脸资料</h2><span className="member-sample-count">{member.face_sample_count}/{limit} 张</span></div>
    <p>建议录入 3–5 张清晰正脸、稍向左和向右的照片，每张仅包含这位成员。支持 JPEG / PNG，每张最多 5 MiB。</p>
    {disabled && !busy && <p className="member-face-hint">请先保存档案修改，再操作人脸资料。</p>}
    <label className="member-consent"><input type="checkbox" checked={consent} disabled={locked || !ready} onChange={event => setConsent(event.target.checked)}/><span>已取得该成员同意，将人脸资料用于本家庭监护</span></label>
    <input ref={uploadInput} type="file" accept="image/jpeg,image/png" multiple hidden aria-label="录入人脸照片" disabled={locked || !ready || !consent} onChange={event => { const files = Array.from(event.currentTarget.files || []); event.currentTarget.value = ''; void enroll(files); }}/>
    <input ref={testInput} type="file" accept="image/jpeg,image/png" hidden aria-label="选择试识别照片" disabled={locked || !ready} onChange={event => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ''; void recognize(file); }}/>
    <div className="member-actions"><button className="primary" disabled={locked || !ready || !consent || member.face_sample_count >= limit} onClick={() => uploadInput.current?.click()}>{busy ? '正在处理…' : '选择照片录入'}</button><button disabled={locked || !ready} onClick={() => testInput.current?.click()}>选择照片试识别</button><button className="danger" disabled={locked || member.face_sample_count === 0} onClick={() => void clearSamples()}>清除人脸资料</button></div>
    <p className="member-face-hint">电脑保存人脸特征，不保留录入原照。试识别会与全部已录入成员比对，不会新增样本。</p>
    {error && <p className="members-error" role="alert">{error}</p>}{notice && <p className="members-notice" role="status">{notice}</p>}
    {testFile && <div className="member-recognition"><div className="member-test-photo">{preview && <img src={preview} alt="本次试识别照片"/>}{result?.faces.map((face, index) => <span className={`member-face-box ${face.status === 'matched' ? 'matched' : ''}`} key={index} style={{ left: `${face.bbox[0] * 100}%`, top: `${face.bbox[1] * 100}%`, width: `${(face.bbox[2] - face.bbox[0]) * 100}%`, height: `${(face.bbox[3] - face.bbox[1]) * 100}%` }}><b>{index + 1}</b></span>)}</div>
      {result && <div role="status"><strong>{result.face_count ? `检测到 ${result.face_count} 张人脸` : '未检测到可用人脸，请换一张清晰照片。'}</strong><ul>{result.faces.map((face, index) => <li key={index}>{index + 1} · {faceIdentityLabel(face)}</li>)}</ul></div>}
      <button disabled={busy} onClick={() => { setTestFile(null); setResult(null); }}>收起试识别</button>
    </div>}
  </section>;
}
