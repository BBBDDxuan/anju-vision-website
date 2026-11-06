export type View = 'home' | 'camera' | 'videos' | 'members';
export type InspectorTab = 'events' | 'qa' | 'tools';

export function inspectorFromUrl(url: URL): InspectorTab {
  const legacy: Record<string, InspectorTab> = { '#timeline': 'events', '#ask': 'qa', '#analysis-tools': 'tools' };
  if (legacy[url.hash]) return legacy[url.hash];
  const panel = url.searchParams.get('panel');
  return panel === 'qa' || panel === 'tools' ? panel : 'events';
}

export const workspaceHref = (view: Exclude<View, 'home'>) => `/workspace?view=${view}`;

export function viewFromUrl(url: URL): View {
  // Existing video links and /workspace continue to open the recording tools.
  const video = url.searchParams.get('video') || '';
  if (url.pathname !== '/workspace' && !/^v_[A-Za-z0-9_-]{1,80}$/.test(video)) return 'home';
  const view = url.searchParams.get('view');
  return view === 'camera' || view === 'members' ? view : 'videos';
}
