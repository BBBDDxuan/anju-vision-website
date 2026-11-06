import { createContext, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import QRCode from 'qrcode';
import './account.css';

type User = { id: string; username: string; display_name: string; household_name: string; role: 'admin' | 'member'; disabled?: number };
export const AccountContext = createContext<User | null>(null);
export const AccountActionsContext = createContext<{
  busy: boolean; message: string; logout: () => Promise<void>; openManagement: () => void;
} | null>(null);

class AuthError extends Error {
  constructor(readonly status: number, message: string) { super(message); }
}

async function auth(path: string, body?: object) {
  const response = await fetch('/api/auth/' + path, {
    method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(12000),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new AuthError(response.status, data.error?.message || '账号服务暂不可用。');
  return data;
}

export function AccountGate({ children }: { children: ReactNode }) {
  const [loaded, setLoaded] = useState(false);
  const [legacy, setLegacy] = useState(false);
  const [setup, setSetup] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [manage, setManage] = useState(false);
  const [demo, setDemo] = useState(false);
  useEffect(() => {
    let active = true;
    setLoaded(false);
    setMessage('');
    async function initialize() {
      try {
        const status = await auth('status');
        if (!active) return;
        if (status.enabled === false && status.cloud === true) { setLegacy(true); setLoaded(true); return; }
        if (status.enabled !== true) throw new Error('请用安居视界本机启动脚本运行账号服务。');
        setSetup(status.setup_allowed === true);
        setDemo(status.insecure_lan_demo === true);
        try {
          const me = await auth('me');
          if (active) setUser(me.user);
        } catch (error) {
          if (!(error instanceof AuthError && error.status === 401)) throw error;
        }
        if (active) setLoaded(true);
      } catch (error) {
        if (active) setMessage(error instanceof Error ? error.message : '无法连接账号服务。');
      }
    }
    void initialize();
    return () => { active = false; };
  }, [refresh]);
  useEffect(() => {
    if (!user) return;
    const check = () => void auth('me').catch(error => {
      if (error instanceof AuthError && error.status === 401) {
        setManage(false); setUser(null); setMessage('登录已失效，请重新登录。');
      }
    });
    const timer = window.setInterval(check, 15000);
    window.addEventListener('focus', check);
    return () => { clearInterval(timer); window.removeEventListener('focus', check); };
  }, [user]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true); setMessage('');
    try {
      if (setup && form.get('password') !== form.get('confirm')) {
        throw new Error('两次输入的密码不一致。');
      }
      const body = { username: form.get('username'), password: form.get('password'),
        ...(setup ? { display_name: form.get('display_name'), household_name: form.get('household_name') } : {}) };
      setUser((await auth(setup ? 'setup' : 'login', body)).user);
      setSetup(false);
    } catch (error) { setMessage(error instanceof Error ? error.message : '操作失败。'); }
    finally { setBusy(false); }
  }

  async function logout() {
    setBusy(true); setMessage('');
    try { await auth('logout', {}); }
    catch (error) {
      if (!(error instanceof AuthError && error.status === 401)) {
        setMessage('未能退出登录，请检查连接后重试。'); setBusy(false); return;
      }
    }
    setManage(false); setUser(null); setBusy(false);
  }

  if (legacy) return children;
  if (user) return <AccountContext.Provider value={user}>
    <AccountActionsContext.Provider value={{ busy, message, logout, openManagement: () => setManage(true) }}>
      {children}
      {manage && <FamilyAdmin admin={user.role === 'admin'} close={() => setManage(false)} />}
    </AccountActionsContext.Provider>
  </AccountContext.Provider>;
  return <main className="account-entry">
    <div className="account-card">
      <span className="account-brand">安居视界</span>
      <h1>{setup ? '首次设置' : '登录家庭账号'}</h1>
      {loaded ? <>
        <p>{setup ? '在这台电脑上创建家庭和唯一的初始管理员。' : '管理员和家人使用同一个登录入口。'}</p>
        {demo && <p className="account-warning">当前为局域网 HTTP 演示模式，请勿使用真实密码或在不可信网络使用。</p>}
        <form onSubmit={submit}>
          {setup && <>
            <label>家庭名称<input name="household_name" required maxLength={60} /></label>
            <label>显示名称<input name="display_name" required maxLength={40} /></label>
          </>}
          <label>用户名<input name="username" required pattern="[A-Za-z0-9_.\-]{3,32}" maxLength={32} autoComplete="username" /></label>
          <label>密码<input name="password" type="password" required minLength={setup ? 12 : 1} maxLength={128} autoComplete={setup ? 'new-password' : 'current-password'} /></label>
          {setup && <label>确认密码<input name="confirm" type="password" required minLength={12} maxLength={128} autoComplete="new-password" /></label>}
          <button type="submit" disabled={busy}>{busy ? '处理中…' : setup ? '创建家庭并登录' : '登录'}</button>
        </form>
        {!setup && <p>还没有账号？请让家庭管理员发邀请，在安居视界 App 扫码注册。</p>}
      </> : <p>{message ? '账号服务未就绪。' : '正在检查账号服务…'}</p>}
      {message && <p role="alert">{message}</p>}
      {!loaded && message && <button onClick={() => setRefresh(value => value + 1)}>重试</button>}
    </div>
  </main>;
}

function FamilyAdmin({ admin, close }: { admin: boolean; close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
    return () => { if (element?.open) element.close(); };
  }, []);
  const [users, setUsers] = useState<User[]>([]);
  const [qr, setQr] = useState('');
  const [mode, setMode] = useState<'invite' | 'connection-code'>('invite');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const list = async () => setUsers(admin ? (await auth('users')).users : (await auth('family-members')).members);
  useEffect(() => { void list().catch(() => setMessage('无法加载家庭账号。')); }, []);
  async function run(action: () => Promise<void>) {
    setBusy(true); setMessage('');
    try { await action(); }
    catch (error) { setMessage(error instanceof Error ? error.message : '操作失败。'); }
    finally { setBusy(false); }
  }
  function create(kind: 'invite' | 'connection-code') {
    setQr(''); setMode(kind);
    void run(async () => {
      const data = await auth(kind, kind === 'invite' ? {} : undefined);
      setQr(await QRCode.toDataURL(JSON.stringify(data), { width: 224 }));
    });
  }
  return <dialog ref={dialog} className="account-admin" aria-labelledby="account-admin-title" onCancel={event => { event.preventDefault(); close(); }}>
    <div className="account-toolbar"><h2 id="account-admin-title">注册家庭成员</h2><button type="button" onClick={close}>关闭</button></div>
    <p>这里显示已注册的家庭账号，不是被监护人员档案。所有有效账号都可查看家庭告警。</p>
    {admin && <div className="account-toolbar">
      <button type="button" disabled={busy} onClick={() => create('invite')}>生成家人邀请二维码</button>
      <button type="button" disabled={busy} onClick={() => create('connection-code')}>生成电脑地址二维码</button>
    </div>}
    {qr && <><p>{mode === 'invite' ? '请用 App「扫码加入家庭」。邀请仅限使用一次，十分钟内有效。' : '请用 App「扫码填写地址」。此码只包含地址，不授予权限。'}</p><img src={qr} width={224} height={224} alt="安居视界家庭二维码" /></>}
    {message && <p role="alert">{message}</p>}
    <ul>{users.map(item => <li key={item.id}>
      {item.display_name}{admin && item.username ? "（" + item.username + "）" : null} · {item.role === 'admin' ? '管理员' : '家庭成员'}{item.disabled ? ' · 已停用' : ''}
      {admin && item.role === 'member' && !item.disabled && <button type="button" disabled={busy} onClick={() => {
        if (window.confirm('停用此账号并撤销其全部登录会话？')) void run(async () => {
          await auth('users/' + item.id + '/revoke', {}); await list();
        });
      }}>停用</button>}
    </li>)}</ul>
  </dialog>;
}
