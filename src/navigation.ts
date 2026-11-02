export type View = 'home' | 'camera' | 'videos';

export const workspaceHref = (view: Exclude<View, 'home'>) => `/workspace?view=${view}`;

export function viewFromUrl(url: URL): View {
  // Existing video links and /workspace continue to open the recording tools.
  const video = url.searchParams.get('video') || '';
  if (url.pathname !== '/workspace' && !/^v_[A-Za-z0-9_-]{1,80}$/.test(video)) return 'home';
  return url.searchParams.get('view') === 'camera' ? 'camera' : 'videos';
}
