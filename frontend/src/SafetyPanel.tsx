import { useEffect, useRef, useState } from 'react';
import { stamp } from './ui';
import { isPriorityEvent } from './confidence';
import { eventTitle, eventSentence } from './familyPresentation';
import { faceIdentityLabel, type FaceIdentity } from './memberApi';
import './safety.css';

type Alert = { id: string; action: string; start_ms: number; end_ms: number; acknowledged: boolean; is_mock: boolean; face_identity?: FaceIdentity };
type AlertData = { alerts: Alert[] };
type Preferences = { fall: boolean; help_gesture: boolean; flame: boolean; browser: boolean };
const preferenceKey = 'anju-reminders-v1';
const defaults: Preferences = { fall: true, help_gesture: true, flame: true, browser: false };
function savedPreferences(): Preferences {
  try {
    const value = JSON.parse(localStorage.getItem(preferenceKey) || '{}');
    return Object.fromEntries(Object.entries(defaults).map(([key, fallback]) => [key, typeof value?.[key] === 'boolean' ? value[key] : fallback])) as Preferences;
  } catch { return { ...defaults }; }
}
const browserPermission = () => typeof Notification === 'undefined' ? 'unsupported' as const : Notification.permission;

export function SafetyPanel({ videoId, running, onSeek, readOnly = false }: { videoId: string; running: boolean; onSeek: (ms: number) => void; readOnly?: boolean }) {
  const [data, setData] = useState<AlertData | null>(null);
  const [error, setError] = useState('');
  const [pending, setPending] = useState('');
  const [permission, setPermission] = useState(browserPermission);
  const [requesting, setRequesting] = useState(false);
  const [preferences, setPreferences] = useState(savedPreferences);
  const preferencesRef = useRef(preferences); preferencesRef.current = preferences;
  const seen = useRef(new Set<string>());
  const initialized = useRef(false);
  const currentVideo = useRef(videoId); currentVideo.current = videoId;
  useEffect(() => {
    try { localStorage.setItem(preferenceKey, JSON.stringify(preferences)); }
    catch { setError('浏览器无法保存提醒设置，本次打开期间仍然有效。'); }
  }, [preferences]);
  useEffect(() => {
    const update = () => setPermission(browserPermission());
    window.addEventListener('focus', update);
    return () => window.removeEventListener('focus', update);
  }, []);
  useEffect(() => { seen.current.clear(); initialized.current = false; setData(null); setError(''); setPending(''); }, [videoId]);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const response = await fetch(`/api/videos/${videoId}/alerts`, { signal: AbortSignal.timeout(15000) });
        if (!response.ok) throw new Error('暂时无法读取提醒，请稍后重试。');
        const next: AlertData = await response.json();
        if (cancelled) return;
        for (const alert of next.alerts) {
          const preference = preferencesRef.current;
          const categoryEnabled = preference[alert.action as keyof Preferences] !== false;
          if (initialized.current && !seen.current.has(alert.id) && !alert.acknowledged && isPriorityEvent(alert) && categoryEnabled && preference.browser && browserPermission() === 'granted') {
            try {
              const notification = new Notification(`${alert.is_mock ? '界面测试 · ' : ''}录像提醒：${eventTitle(alert.action)}`, { body: `视频 ${stamp(alert.start_ms)}，${eventSentence(alert.action)}这是录像中的事件。`, tag: `${videoId}:${alert.id}` });
              notification.onclick = () => { window.focus(); if (!cancelled) onSeek(alert.start_ms); notification.close(); };
            } catch { setError('系统通知暂时无法显示，仍可在这里查看提醒。'); }
          }
          seen.current.add(alert.id);
        }
        initialized.current = true; setData(next);
      } catch (cause) { if (!cancelled) setError((cause as Error).message); }
      finally { if (!cancelled && running) timer = setTimeout(load, 2000); }
    };
    void load();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [videoId, running]);
  const toggleNotifications = async (enabled: boolean) => {
    if (!enabled) { setPreferences(p => ({ ...p, browser: false })); return; }
    setRequesting(true);
    try {
      const result = typeof Notification === 'undefined' ? 'unsupported' : await Notification.requestPermission();
      setPermission(result); setPreferences(p => ({ ...p, browser: result === 'granted' }));
    } catch { setError('未能开启浏览器通知，请检查浏览器设置。'); }
    finally { setRequesting(false); }
  };
  const acknowledge = async (alert: Alert) => {
    setPending(alert.id); const id = videoId;
    try {
      const response = await fetch(`/api/videos/${id}/alerts/${alert.id}/acknowledge`, { method: 'POST', signal: AbortSignal.timeout(15000) });
      if (!response.ok) throw new Error('查看状态保存失败，请重试。');
      if (currentVideo.current === id) setData(current => current && ({ ...current, alerts: current.alerts.map(a => a.id === alert.id ? { ...a, acknowledged: true } : a) }));
    } catch (cause) { if (currentVideo.current === id) setError((cause as Error).message); }
    finally { if (currentVideo.current === id) setPending(''); }
  };
  const alerts = (data?.alerts || []).filter(isPriorityEvent);
  return <section className="family-reminders" aria-label="录像提醒中心">
    <div className="inspector-section-heading"><h2>录像提醒</h2><span>{alerts.filter(a => !a.acknowledged).length} 条待查看</span></div>
    <details className="reminder-settings"><summary>提醒设置</summary>
      {([['fall','跌倒提醒'],['help_gesture','挥手提醒'],['flame','明火提醒']] as const).map(([key, label]) => <label className="reminder-switch" key={key}><span>{label}</span><input type="checkbox" role="switch" checked={preferences[key]} onChange={e => setPreferences(p => ({ ...p, [key]: e.target.checked }))}/></label>)}
      <label className="reminder-switch"><span>浏览器通知</span><input type="checkbox" role="switch" checked={preferences.browser && permission === 'granted'} disabled={requesting || permission === 'unsupported'} onChange={e => void toggleNotifications(e.target.checked)}/></label>
      <p role="status">{permission === 'unsupported' ? '此浏览器不支持系统通知。' : permission === 'denied' ? '通知权限已被浏览器阻止，请在网站设置中允许通知。' : permission === 'granted' ? preferences.browser ? '浏览器通知已开启。' : '浏览器已允许通知，当前开关已关闭。' : '开启后，浏览器会请求通知权限。'}</p>
      <p>开关控制新提醒的浏览器通知，已生成的录像提醒仍会保留。请保持网页打开。</p>
    </details>
    {error && <p className="warning" role="status">{error}</p>}
    {!data && !error ? <p className="empty">正在读取提醒…</p> : !alerts.length ? <p className="empty">目前没有需要查看的提醒。</p> : <div className="reminder-list">{alerts.map(alert => <article className={`reminder-card${alert.acknowledged ? ' is-viewed' : ''}`} key={alert.id}>
      <div className="family-event-heading"><strong>{eventTitle(alert.action)}</strong><span>{alert.acknowledged ? '已查看' : '待查看'}</span></div><time>视频 {stamp(alert.start_ms)}–{stamp(alert.end_ms)}</time>
      {alert.face_identity && ['fall', 'help_gesture'].includes(alert.action) && <p className="alert-member-identity">成员：{faceIdentityLabel(alert.face_identity)}</p>}
      <p>{eventSentence(alert.action)}</p><div className="reminder-actions"><button className="reference" onClick={() => onSeek(alert.start_ms)}>查看视频</button>{!readOnly && !alert.acknowledged && <button className="text-button" disabled={!!pending} onClick={() => void acknowledge(alert)}>{pending === alert.id ? '正在保存…' : '标记为已查看'}</button>}</div>
    </article>)}</div>}
  </section>;
}
