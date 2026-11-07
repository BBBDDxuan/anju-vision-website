import { useContext, useEffect, useRef, useState } from 'react';
import QRCode from 'qrcode';
import { AccountContext } from './AccountGate';
import './lan-setup.css';

type Camera = { host: string; port: number; username: string; path: string };
type Panel = 'pair' | 'camera';

async function lan(path: string, body?: object) {
  const response = await fetch(path, {
    method: body ? 'POST' : 'GET', cache: 'no-store', signal: AbortSignal.timeout(18000),
    ...(body && { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error?.message || `操作失败（HTTP ${response.status}）。`);
  return data;
}

export function LanSetup() {
  const [panel, setPanel] = useState<Panel | null>(null);
  const [qr, setQr] = useState('');
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [host, setHost] = useState('');
  const [port, setPort] = useState('554');
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [path, setPath] = useState('stream1');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  const account = useContext(AccountContext);
  const loopback = !account && ['127.0.0.1', 'localhost', '[::1]'].includes(location.hostname);
  useEffect(() => {
    if (panel && !dialog.current?.open) dialog.current?.showModal();
    else if (!panel) dialog.current?.close();
  }, [panel]);

  function toggle(next: Panel) {
    setPanel(panel === next ? null : next);
    setMessage('');
    setQr('');
    setPassword('');
  }

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setMessage('');
    try { await action(); }
    catch (error) { setMessage(error instanceof Error ? error.message : '操作失败'); }
    finally { setBusy(false); }
  }
  const discover = () => run(async () => {
    setCameras([]);
    const result = await lan('/api/lan/cameras');
    setCameras(result.cameras);
    if (!result.cameras.length) setMessage('暂未发现摄像头。请确认设备与电脑在同一网络，或手动填写设备地址。');
  });

  if (account?.role === 'member') return null;
  return <section className="lan-setup" aria-label="局域网快捷连接">
    <div className="lan-setup-actions">
      {loopback && <button type="button" id="lan-pair-button" disabled={busy}
        aria-expanded={panel === 'pair'} aria-controls="lan-pair-panel" onClick={() => toggle('pair')}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3h6v6H3zm12 0h6v6h-6zM3 15h6v6H3zm12 0v3h3m3-3v6h-6"/></svg>
        扫码连接手机
      </button>}
      <button type="button" id="lan-camera-button" disabled={busy}
        aria-expanded={panel === 'camera'} aria-controls="lan-camera-panel" onClick={() => { toggle('camera'); void discover(); }}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/></svg>
        搜索摄像头
      </button>
    </div>
    <dialog ref={dialog} className="lan-dialog" aria-labelledby="lan-dialog-title" onClose={() => { setPanel(null); setPassword(''); }} onCancel={() => { setPanel(null); setPassword(''); }}><div className="lan-dialog-heading"><h2 id="lan-dialog-title">{panel === 'pair' ? '连接手机' : '添加摄像头'}</h2><button type="button" aria-label="关闭连接窗口" onClick={() => { setPanel(null); setPassword(''); }}>关闭</button></div>
    {loopback && <div id="lan-pair-panel" hidden={panel !== 'pair'} className="lan-setup-panel" aria-labelledby="lan-pair-button">
      <p>在安居视界 App 中扫码。二维码两分钟内有效，仅可使用一次，请勿分享。</p>
      <button type="button" disabled={busy} onClick={() => void run(async () => {
        setQr('');
        setQr(await QRCode.toDataURL(JSON.stringify(await lan('/api/lan/pair', {})), { width: 240 }));
      })}>{qr ? '重新生成二维码' : '生成配对二维码'}</button>
      {qr && <img className="lan-setup-qr" src={qr} width={240} height={240} alt="安居视界手机配对二维码"/>}
    </div>}
    <div id="lan-camera-panel" hidden={panel !== 'camera'} className="lan-setup-panel" aria-labelledby="lan-camera-button">
      <p>搜索同一网络中支持自动发现的摄像头。选择设备并填写密码后，点击下方按钮开启画面。</p>
      <button type="button" disabled={busy} onClick={() => void discover()}>{busy ? '正在搜索…' : '重新搜索设备'}</button>
      {cameras.length > 0 && <ul className="lan-setup-results">{cameras.map(camera => <li key={camera.host}><button type="button" disabled={busy} onClick={() => {
        setHost(camera.host); setPort(String(camera.port)); setUsername(camera.username); setPath(camera.path); setPassword('');
      }}>{camera.host}</button></li>)}</ul>}
      <form onSubmit={event => {
        event.preventDefault();
        void run(async () => {
          await lan('/api/monitoring/camera', { host: host.trim(), port: Number(port), username: username.trim(), password, path: path.trim() });
          setPassword('');
          setMessage('摄像头已连接，实时看护已开启。关闭窗口即可查看画面。');
        });
      }}>
        <fieldset disabled={busy}>
          <label>设备地址（IP）<input required value={host} onChange={e => setHost(e.target.value)} /></label>
          <label>RTSP 端口 <input required type="number" min={1} max={65535} value={port} onChange={e => setPort(e.target.value)} /></label>
          <label>用户名 <input required value={username} onChange={e => setUsername(e.target.value)} /></label>
          <label>设备密码 <input required type="password" autoComplete="off" value={password} onChange={e => setPassword(e.target.value)} /></label>
          <label>视频路径 <input required value={path} onChange={e => setPath(e.target.value)} /></label>
          <button type="submit">连接并开启摄像头</button>
        </fieldset>
      </form>
    </div>
    <p className="lan-setup-status" role="status" hidden={!busy && !message}>{busy ? '处理中…' : message}</p>
    </dialog>
  </section>;
}
