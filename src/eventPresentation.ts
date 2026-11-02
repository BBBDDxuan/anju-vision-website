type Entity = { id: string; label: string; entity_type?: string };
type Event = { subject_id: string; target_id: string | null; action: string };
const active: Record<string,string> = {pick_up:'拿起',place_on:'放下',open:'打开',close:'关闭'};
export const isEnvironmentEvent = (event: {action: string}) => ['smoke','flame','sparks'].includes(event.action);

export function replayDescription(entry: { event: Event; epistemic_type: string; rule_id?: string | null; condition?: string },
  label: (id: string | null) => string, entityType: (id: string) => string | undefined, actionNames: Record<string,string>) {
  // Container movement is conditional on contents staying inside, not a direct event for the selected item.
  if (entry.rule_id === 'conditional_containment_v1' && entry.condition) return entry.condition;
  return `${entry.epistemic_type === 'inferred' ? '可能：' : ''}${eventDescription(entry.event,label,entityType,actionNames)}`;
}

export function ambiguousPersonRelation(event: Event, entityType: (id: string) => string | undefined) {
  return entityType(event.subject_id) === 'person' && ['seen_at','move','walk'].includes(event.action)
    && !!event.target_id && entityType(event.target_id) !== 'location';
}

export function eventClass(event: Event, entities: Map<string, Entity>): 'object' | 'person_general' | 'person_important' | 'environment' | 'support' {
  if (event.action === 'no_person_visible') return 'support';
  if (isEnvironmentEvent(event)) return 'environment';
  if (['fall','help_gesture','long_absence'].includes(event.action)) return 'person_important';
  return entities.get(event.subject_id)?.entity_type === 'person' ? 'person_general' : 'object';
}

export function eventDescription(event: Event, label: (id: string | null) => string,
  entityType: (id: string) => string | undefined, actionNames: Record<string,string>) {
  const subject = label(event.subject_id), target = label(event.target_id);
  const hazard: Record<string,string> = {smoke:'出现烟雾迹象',flame:'出现明火迹象',sparks:'出现火花／电弧迹象'};
  if (hazard[event.action]) return `${subject} ${hazard[event.action]}`;
  const person = entityType(event.subject_id) === 'person';
  const context = !target ? '' : event.target_id && entityType(event.target_id) === 'location'
    ? `（位置参照：${target}）` : `（关联物体：${target}；具体关系未明确）`;
  if (person && active[event.action]) return `${subject} ${active[event.action]}${target || '（被操作物体未明确）'}`;
  if (event.action === 'seen_at') {
    return `${subject} 出现在${!person && target ? target : '画面中'}${person ? context : ''}`;
  }
  if (event.action === 'move') {
    if (person && ambiguousPersonRelation(event,entityType)) return `${subject} 有与${target}相关的活动（具体动作关系未明确）`;
    return `${subject} ${person ? '在画面中移动' : '位置发生变化'}${context || '（去向未明确）'}`;
  }
  if (person) {
    const verbs: Record<string,string> = {walk:'在画面中行走',sit_down:'坐下',stand_up:'起身',lie_down:'躺下',
      lying_on_floor:'躺卧于地面',fall:'出现疑似跌倒过程',climb:'攀爬',help_gesture:'做出疑似求助动作',
      explicit_exit:'离开摄像头视野',occluded:'被遮挡',unknown:'的相关动作无法确定'};
    if (verbs[event.action]) return `${subject} ${verbs[event.action]}${['walk','sit_down','stand_up','lie_down','climb'].includes(event.action) ? context : ''}`;
    if (['put_in','take_out'].includes(event.action)) return `${subject} 有与${target || '物体'}相关的进出候选（主体与容器关系未明确）`;
  }
  const phrases: Record<string,string> = {
    pick_up:`被拿起${target ? `（相关对象：${target}）` : ''}`,
    place_on:target ? `被放在${target}上` : '被放下（位置未明确）',
    put_in:target ? `被放入${target}` : '存在放入候选（容器未明确）',
    take_out:target ? `被从${target}取出` : '存在取出候选（容器未明确）',
    open:'被打开',close:'被关闭',explicit_exit:'离开摄像头视野',occluded:'被遮挡',
    unknown:'的相关动作无法确定',no_person_visible:'的所引用采样画面未见人物',
  };
  return `${subject} ${phrases[event.action] || actionNames[event.action] || '出现活动（动作描述未明确）'}`;
}
