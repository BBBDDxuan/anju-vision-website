"""Person actors and manipulated objects have different grammatical/state roles."""
INTERACTION_ACTIONS = {'pick_up': '拿起', 'place_on': '放下', 'open': '打开', 'close': '关闭'}


def person_interaction(event, entities):
    return entities[event['subject_id']].get('entity_type') == 'person' and event['action'] in INTERACTION_ACTIONS


def interaction_description(event, entities):
    target = entities.get(event['target_id'], {}).get('label')
    return f"{entities[event['subject_id']]['label']}{INTERACTION_ACTIONS[event['action']]}{target or '（被操作物体未明确）'}"


def ambiguous_person_relation(event, entities):
    return (entities[event['subject_id']].get('entity_type') == 'person'
            and event['action'] in {'seen_at', 'move', 'walk'} and event.get('target_id') is not None
            and entities.get(event['target_id'], {}).get('entity_type') != 'location')


def event_description(event, entities):
    subject = entities[event['subject_id']]['label']
    target_entity = entities.get(event['target_id'], {})
    target = target_entity.get('label', '')
    person = entities[event['subject_id']].get('entity_type') == 'person'
    context = '' if not target else (f'（位置参照：{target}）' if target_entity.get('entity_type') == 'location'
                                     else f'（关联物体：{target}；具体关系未明确）')
    if person_interaction(event, entities):
        return interaction_description(event, entities)
    action = event['action']
    hazards = {'smoke': '出现烟雾迹象', 'flame': '出现明火迹象', 'sparks': '出现火花／电弧迹象'}
    if action in hazards:
        return subject+hazards[action]
    if action == 'seen_at':
        return f"{subject}出现在{target if target and not person else '画面中'}{context if person else ''}"
    if action == 'move':
        if ambiguous_person_relation(event, entities):
            return f'{subject}有与{target}相关的活动（具体动作关系未明确）'
        return f"{subject}{'在画面中移动' if person else '位置发生变化'}{context or '（去向未明确）'}"
    if person:
        verbs = {'walk':'在画面中行走','sit_down':'坐下','stand_up':'起身','lie_down':'躺下','lying_on_floor':'躺卧于地面',
                 'fall':'出现疑似跌倒过程','climb':'攀爬','help_gesture':'做出疑似求助动作',
                 'explicit_exit':'离开摄像头视野','occluded':'被遮挡','unknown':'的相关动作无法确定'}
        if action in verbs:
            return subject+verbs[action]+(context if action in {'walk','sit_down','stand_up','lie_down','climb'} else '')
        if action in {'put_in', 'take_out'}:
            return f'{subject}有与{target or "物体"}相关的进出候选（主体与容器关系未明确）'
    phrases = {'pick_up': '被拿起'+(f'（相关对象：{target}）' if target else ''),
               'place_on': f'被放在{target}上' if target else '被放下（位置未明确）',
               'put_in': f'被放入{target}' if target else '存在放入候选（容器未明确）',
               'take_out': f'被从{target}取出' if target else '存在取出候选（容器未明确）',
               'open':'被打开','close':'被关闭','explicit_exit':'离开摄像头视野','occluded':'被遮挡',
               'unknown':'的相关动作无法确定','no_person_visible':'的所引用采样画面未见人物'}
    return subject+phrases.get(action, '出现活动（动作描述未明确）')
