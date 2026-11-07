import { eventDescription } from './eventPresentation';
import type { ConfidenceIndex, DisplayClaim } from './confidence';

export const commonQuestions = ['视频中有没有人摔倒？', '有没有人挥手？', '有没有发现明火？', '视频中有几个人？', '帮我找到相关视频片段。'];
export const unknownAnswer = '这段视频中暂时没有找到相关信息。';
export const familyEntityLabel = (label: string) => label.replace(/明火候选位置/g, '火焰区域').replace(/人物候选/g, '人物');
export const eventTitle = (action: string) => ({ fall: '跌倒提醒', help_gesture: '挥手动作', flame: '明火提醒', smoke: '烟雾提醒', sparks: '火花提醒', long_absence: '较长时间未出现', seen_at: '出现在画面中', move: '位置变化', walk: '行走', sit_down: '坐下', stand_up: '起身', lie_down: '躺下', lying_on_floor: '躺卧在地面', climb: '攀爬', pick_up: '拿起物品', place_on: '放下物品', put_in: '放入物品', take_out: '取出物品', open: '打开', close: '关闭', explicit_exit: '离开画面', occluded: '被遮挡' }[action] || '相关活动');
export const eventSentence = (action: string) => ({ fall: '检测到跌倒姿态，请及时关注。', help_gesture: '检测到挥手动作。', flame: '检测到明火，请及时关注。', smoke: '检测到烟雾，请及时关注。', sparks: '检测到火花，请及时关注。', long_absence: '人物较长时间没有出现在已分析画面中。' }[action] || '查看这段录像中的相关活动。');

// Older saved answers may contain diagnostic prose. Keep their grounded source
// events, and present an event description instead of exposing pipeline details.
const internalLanguage = /已有有效直接观察|位置边界|模型候选|人物候选|小模型|Qwen|推理链|置信度|非历史确认|历史核验|历史确认|复核|直接观察记录|采样记录|容器关系|线索|(?:请|需)结合|自行核实|仅供参考|不能据此|不代表/;
export function familyClaim(claim: DisplayClaim, index: ConfidenceIndex) {
  const sources = (claim.event_ids || []).flatMap(id => { const source = index.events.get(id); return source ? [source] : []; });
  if (!sources.length) return unknownAnswer;
  const wave = sources.some(e => e.action === 'help_gesture');
  let text = claim.text;
  if (internalLanguage.test(text) || wave && /求助|求救/.test(text)) {
    text = [...new Set(sources.map(e => ['fall','help_gesture','flame','smoke','sparks','long_absence'].includes(e.action) ? eventSentence(e.action) : `${e.epistemic_type === 'inferred' ? '可能：' : ''}${eventDescription(e, id => familyEntityLabel(id ? index.entities.get(id)?.label || '' : ''), id => index.entities.get(id)?.entity_type, {})}。`))].join('');
  }
  // Describe the recognized appearance, without asserting injury or intent.
  if (sources.some(e => e.action === 'fall')) text = text.replace(/疑似(?:跌倒|摔倒)(?:过程|动作)?/g, '跌倒姿态');
  if (sources.some(e => e.action === 'flame')) text = text.replace(/疑似明火/g, '明火');
  return text;
}

export function familyUnknown(text: string) {
  if (/尚未完整|未完成|部分完成/.test(text)) return '分析还未全部完成，回答只覆盖已分析的片段。';
  if (/静止时段|静止过滤|间隔检查|未覆盖|识别缺口|漏检|遗漏/.test(text)) return '部分时段的画面信息不足。';
  if (/音频|声音|说话/.test(text)) return '这段录像没有足够的声音信息，暂时无法判断相关情况。';
  if (/当前位置|容器|遮挡/.test(text)) return '只能参考录像中可见的位置，暂时无法确认现在的位置。';
  if (/没有人物|未见人物|0人|0 人/.test(text)) return '目前没有识别到清晰的人物画面。';
  if (/人数|匿名ID|独立人数/.test(text)) return '不同片段中可能是同一位家人，整段录像的人数暂时无法确认。';
  return unknownAnswer;
}
