"""Category aliases only: never merge object identities or invent visual facts."""
import copy
import re

VOCABULARY = [
    (r'\b(laptop|notebook computer)\b', '笔记本电脑', ['电脑', '笔记本']),
    (r'\b(headset|headphones?)\b', '耳机', ['头戴式耳机']),
    (r'\b(backpack|rucksack)\b', '背包', ['双肩包']),
    (r'\b(smartphone|mobile phone|cell phone)\b', '手机', []),
    (r'\b(charger|charging adapter)\b', '充电器', ['电源适配器']),
    (r'\b(remote|remote control)\b', '遥控器', []),
    (r'\b(thermos|bottle)\b', '水杯', ['保温杯', '水瓶']),
    (r'\b(mouse)\b', '鼠标', []),
    (r'\b(can|tin)\b', '罐子', ['圆罐']),
    (r'\b(table|desk)\b', '桌面', ['桌子', '会议桌']),
    (r'\b(shelf|shelving unit)\b', '置物架', ['柜子']),
]


def localized_document(document):
    result = copy.deepcopy(document)
    for entity in result['entities']:
        original = entity['label']
        for pattern, label, aliases in VOCABULARY:
            if re.search(pattern, original, re.I) and not re.search(r'[\u4e00-\u9fff]', original):
                # Precise shelf-cell names require calibration; don't erase them by category.
                if re.search(r'row|column|compartment|cell|格', original, re.I):
                    continue
                entity['label'] = label
                entity['aliases'] = list(dict.fromkeys([original, *entity['aliases'], *aliases]))
                break
    return result
