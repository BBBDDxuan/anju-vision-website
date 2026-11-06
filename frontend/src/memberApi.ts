export type Member = {
  id: string; number: number; name: string; relationship: string; notes: string;
  bindings: { device_id: string; scope: string; track_id?: string }[];
  revision: number; face_sample_count: number; label_color?: string; label_letter?: string;
};
export type FaceStatus = { enabled: boolean; ready: boolean; error?: string; max_samples_per_member: number; max_image_bytes: number };
export type FaceIdentity = { status: string; member_id?: string | null; name?: string; reason?: string };
export type RecognizedFace = FaceIdentity & { bbox: number[] };
export type Recognition = { faces: RecognizedFace[]; face_count: number };
export const memberTitle = (member: Member) => member.name.trim() || `成员${member.number}`;

export class MemberApiError extends Error {
  constructor(message: string, public code: string) { super(message); }
}

export async function memberRequest<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, { ...init, cache: 'no-store', signal: init?.signal
      ? AbortSignal.any([init.signal, AbortSignal.timeout(30000)]) : AbortSignal.timeout(30000) });
  } catch (error) {
    if (init?.signal?.aborted) throw error;
    throw new MemberApiError('服务连接失败或请求超时，请检查本机后端后重试。', 'CONNECTION_FAILED');
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new MemberApiError(body?.error?.message || `操作失败（HTTP ${response.status}）`, body?.error?.code || 'REQUEST_FAILED');
  if (body === null) throw new MemberApiError('服务返回了无法读取的结果，请刷新重试。', 'INVALID_RESPONSE');
  return body as T;
}

export function faceFileError(file: File, maxBytes = 5 * 1024 * 1024): string {
  if (!['image/jpeg', 'image/png'].includes(file.type)) return '请选择 JPEG 或 PNG 图片。';
  if (!file.size || file.size > maxBytes) return '每张图片不能为空，且不能超过 5 MiB。';
  return '';
}

export function faceIdentityLabel(identity?: FaceIdentity): string {
  return identity?.status === 'matched' && identity.member_id && identity.name?.trim()
    ? `${identity.name.trim()}（人脸识别候选，请核对）` : '身份未确认';
}

export const faceStatusMessage = (status: FaceStatus | null) => !status ? '人脸服务状态暂不可用'
  : !status.enabled ? '人脸识别未启用'
  : status.ready ? '本地人脸识别已就绪'
  : status.error === 'FACE_MODEL_MISSING' ? '缺少人脸模型，请先准备模型并刷新'
  : status.error === 'FACE_MODEL_INTEGRITY' ? '人脸模型校验失败，请重新准备模型'
  : '人脸模型加载失败，请检查后端环境';
