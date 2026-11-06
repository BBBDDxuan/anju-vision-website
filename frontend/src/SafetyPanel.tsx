import { useEffect, useRef, useState } from 'react';
import { Icon, stamp } from './ui';
import { isEnvironmentEvent } from './eventPresentation';
import { isPriorityEvent } from './confidence';
import { faceIdentityLabel, type FaceIdentity } from './memberApi';
import './safety.css';

type Alert = { id: string; title: string; person_label: string; severity: 'urgent' | 'attention'; action: string;
  start_ms: number; end_ms: number; acknowledged: boolean; evidence_ids: string[]; message: string; is_mock: boolean; epistemic_type?: string; subject_label?: string; reasons?: string[]; face_identity?: FaceIdentity };
type AlertData = { alerts: Alert[]; evidence: { id: string; frame_ids: string[] }[];
  frames: { id: string; pts_ms: number; url: string }[]; absence_alert_seconds: number };

export function SafetyPanel({ videoId, running, onSeek, visible = true, mode = 'all', readOnly = false }: { videoId: string; running: boolean; onSeek: (ms: number) => void; visible?: boolean; mode?: 'all' | 'person' | 'environment'; readOnly?: boolean }) {
  const [data, setData] = useState<AlertData | null>(null);
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState('');
  const [pending, setPending] = useState('');
  const [permission, setPermission] = useState<NotificationPermission | 'unsupported'>(() => typeof Notification === 'undefined' ? 'unsupported' : Notification.permission);
  const seen = useRef(new Set<string>());
  const initialized = useRef(false);
  useEffect(() => { seen.current.clear(); initialized.current = false; setData(null); setExpanded(''); setError(''); }, [videoId]);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const response = await fetch(`/api/videos/${videoId}/alerts`);
        if (!response.ok) throw new Error('预警列表暂时无法读取，请稍后重试。');
        const next: AlertData = await response.json();
        if (cancelled) return;
        for (const alert of next.alerts) {
          if (isPriorityEvent(alert) && initialized.current && !seen.current.has(alert.id) && !alert.acknowledged && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
            try { new Notification(`${alert.is_mock ? '模拟测试 · ' : ''}历史录像提醒：${alert.title}`, {
              body: `${isEnvironmentEvent(alert) ? alert.subject_label || alert.person_label : faceIdentityLabel(alert.face_identity)} · 片段 ${stamp(alert.start_ms)}，请在网页查看证据。非当前现场。`, tag: alert.id,
            }); } catch { /* The persistent on-page alert remains available if browser notifications fail. */ }
          }
          seen.current.add(alert.id);
        }
        initialized.current = true;
        setData(next); setError('');
      } catch (e) { if (!cancelled) setError((e as Error).message); }
      finally { if (!cancelled && running) timer = setTimeout(load, 2000); }
    };
    void load();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [videoId, running]);
  const enableNotifications = async () => {
    try { if (typeof Notification !== 'undefined') setPermission(await Notification.requestPermission()); }
    catch { setError('浏览器通知未能启用，网页预警仍可使用。'); }
  };
  const acknowledge = async (alert: Alert) => {
    setPending(alert.id);
    try {
      const response = await fetch(`/api/videos/${videoId}/alerts/${alert.id}/acknowledge`, { method: 'POST' });
      if (!response.ok) throw new Error('提醒状态保存失败。');
      setData(current => current && ({ ...current, alerts: current.alerts.map(a => a.id === alert.id ? { ...a, acknowledged: true } : a) }));
    } catch (e) { setError((e as Error).message); }
    finally { setPending(''); }
  };
  const alerts = (data?.alerts || []).filter(a => isPriorityEvent(a) && (mode === 'all' || (mode === 'environment' ? isEnvironmentEvent(a) : !isEnvironmentEvent(a))));
  if (!visible) return null;
  return <section className="panel safety-panel" id="safety" aria-label={mode === 'environment' ? '环境危险预警' : '事件预警'}>
    <div className="panel-head"><strong><Icon name="shield" size={18}/>{mode === 'environment' ? '环境危险事件' : mode === 'person' ? '人物重要事件' : '重要事件与危险预警'}</strong><span className="tag">{alerts.filter(a => !a.acknowledged).length} 条待关注</span></div>
    <div className="safety-content"><div className="safety-notification"><span>录像分析提醒 · 随分析进度刷新</span><button className="text-button" onClick={() => void enableNotifications()} disabled={permission !== 'default'}>{permission === 'granted' ? '浏览器通知已启用' : permission === 'denied' ? '浏览器通知被阻止' : permission === 'unsupported' ? '此浏览器不支持系统通知' : '启用浏览器通知'}</button></div>
      <div className="safety-metrics">{mode !== 'environment' && <span><strong>{alerts.filter(a => !isEnvironmentEvent(a)).length}</strong> 人物重要事件</span>}{mode !== 'person' && <span><strong>{alerts.filter(a => isEnvironmentEvent(a)).length}</strong> 环境危险线索</span>}<span><strong>{alerts.filter(a => a.acknowledged).length}</strong> 已知悉</span></div>
      <p className="safety-boundary">红色为优先关注的录像事件，点击回看证据。</p>
      <details className="safety-explainer"><summary>通知设置</summary><p>浏览器通知需保持网页打开。</p>{data && mode !== 'environment' && <p>长时间未出现提醒阈值：{data.absence_alert_seconds/60} 分钟。</p>}</details>
      {error && <p className="warning" role="status">{error}</p>}
      {!data && !error ? <p className="empty">正在读取提醒…</p> : !alerts.length ? <p className="empty">当前类别暂无提醒事件。</p> : <div className="safety-list" aria-live="polite">{alerts.map(alert => {
        const ids = new Set(data?.evidence.filter(e => alert.evidence_ids.includes(e.id)).flatMap(e => e.frame_ids));
        const frames = data?.frames.filter(f => ids.has(f.id)) || [];
        return <article key={alert.id} className={`safety-alert ${alert.severity} ${alert.acknowledged ? 'acknowledged' : ''}`}>
          <div className="safety-alert-heading"><strong>{alert.is_mock ? '模拟测试 · ' : ''}{alert.title}</strong><span>{alert.acknowledged ? '已知悉' : alert.severity === 'urgent' ? '优先关注' : '注意观察'}</span></div>
          <p>{alert.subject_label || alert.person_label} · 视频 {stamp(alert.start_ms)}–{stamp(alert.end_ms)}{alert.epistemic_type === 'inferred' ? ' · 推测/规则提醒' : ''}</p>
          {!isEnvironmentEvent(alert) && <p className="alert-member-identity">成员：{faceIdentityLabel(alert.face_identity)}</p>}
          {!!alert.reasons?.length && <details className="safety-reason"><summary>查看事件依据</summary><small className="reason">{alert.reasons.join('；')}</small></details>}
          <div className="safety-actions"><button className="reference" onClick={() => { onSeek(alert.start_ms); setExpanded(expanded === alert.id ? '' : alert.id); }}>查看视频证据 <Icon name="arrow-up-right" size={13}/></button>{!readOnly && !alert.acknowledged && <button className="text-button" disabled={pending === alert.id} onClick={() => void acknowledge(alert)}>标记已知悉</button>}</div>
          {expanded === alert.id && <div className="safety-frames">{frames.map(frame => <figure key={frame.id}><img src={frame.url} loading="lazy" alt={`事件证据 ${stamp(frame.pts_ms)}`}/><figcaption>{stamp(frame.pts_ms)}</figcaption></figure>)}</div>}
        </article>;
      })}</div>}
    </div>
  </section>;
}
