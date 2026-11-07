export type CameraAction = 'search' | 'connect' | 'start' | 'stop';

const actionLabels: Record<CameraAction, string> = {
  search: '搜索摄像头', connect: '连接摄像头', start: '开启摄像头', stop: '关闭摄像头',
};

export function cameraResponseError(action: CameraAction, status: number, data: unknown): string {
  const error = (data as { error?: { code?: unknown; message?: unknown } } | null)?.error;
  if (status === 401) return '登录已失效，请重新登录后再操作摄像头。';
  if (status === 403) return typeof error?.message === 'string' && error.message
    ? error.message : '没有操作摄像头的权限，请使用管理员账号。';
  if (status === 422 && (!error?.code || error.code === 'INVALID_REQUEST')) {
    return '摄像头参数无效，请核对 IP、RTSP 端口、设备账号、密码和视频路径。';
  }
  // A device-side RTSP 404 has a typed error. A plain HTTP 404 is a missing computer endpoint.
  if (typeof error?.message === 'string' && error.message) return error.message;
  if (status === 404 || status === 405) {
    return '当前电脑服务未提供此摄像头接口，请更新并重启电脑后端。';
  }
  if (status === 429) return '操作过于频繁，请稍后重试。';
  return actionLabels[action] + '失败（HTTP ' + status + '），请检查电脑后端日志。';
}

export function cameraNetworkError(action: CameraAction, error: unknown): string {
  const name = (error as { name?: string } | null)?.name;
  return name === 'TimeoutError' || name === 'AbortError'
    ? actionLabels[action] + '请求超时，请检查电脑后端和摄像头网络。'
    : '无法连接电脑后端，请检查电脑服务是否运行。';
}
