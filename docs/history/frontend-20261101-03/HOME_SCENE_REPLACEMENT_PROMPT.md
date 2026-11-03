最新版本：参见 [老人失衡与独立火情示意图](SCENE_STUMBLE_FIRE.md)。以下保留前序候选制作记录。

# 首页居家场景图制作说明

状态：已生成并接入。采用内置 image_gen 工具，以原始首页图片为风格参考进行修改。

最终资源：`frontend/public/assets/home-care-scene-highlight.png`，1536 × 1024。
风格与构图参考：`frontend/public/assets/home-care-scene-refined.png`。
原始资源：`frontend/public/assets/home-care-scene.png`，保留供回退。
前两张写实与卡通候选不用于页面，保存在 `review/scene-candidates/`。

用户最终要求：与原图画风一致。保留原图柔和、精致的暖色居家效果，调整人物和走道布局，补充清晰可见的钥匙与包。

页面保留 AI 场景标识，使用完整 3:2 比例展示，移除遮挡图片底部的横幅，避免裁掉人物脚部。

## 最终实际生成描述

Edit this provided image, using it as the exact ART STYLE reference and existing scene baseline. The user wants the SAME original visual style, not a cartoon and not a realistic surveillance photograph. Preserve this particular polished AI architectural lifestyle illustration aesthetic: soft warm sunlight, carefully rendered natural proportions, subtly idealized elderly man, textured sage green upholstery, warm wooden furniture, gentle visual finish. Keep the same elderly man with grey hair, green cardigan, beige trousers and cane, same overall room palette and familiar living-room furnishing. Make only practical compositional changes for a home care and object tracking website: slightly raise the room viewpoint to a fixed upper-corner angle with gentle downward perspective; simplify a few distracting plants and ornate decorations, provide clear uncluttered floor around the man and keep his ENTIRE body and cane fully visible, with generous floor below both feet. Move him a little towards the middle, without a facial closeup. Clearly show a simple set of keys and cup on the coffee table and a small blue bag on the nearby chair to suggest object tracking. Keep warm light but avoid harsh spotlight contrast. No cartoon, no chibi, no enlarged head or eyes, no flat vector, no clay-toy character, no documentary photographic realism, no timestamp or detection graphics, no text or logos. Keep the original's attractive softly idealized rendering and detailed yet calm home scene. Landscape 3:2, single coherent image, no border. Do not add any new people.


## 功能高亮修订

用户要求：高亮与系统有关的内容，用红框展示老人跌倒风险，用其他颜色标注物品。

采用内置 image_gen 工具，仅调整老人重心不稳的姿态；红色人物框、蓝色钥匙框、绿色包框由前端按图片百分比位置绘制，随屏幕缩放。无模型调用、置信分数或真实检测结果，页面明确标为功能示意。

实际编辑描述：

Edit only the elderly man's pose in this provided scene. Preserve the original image's exact warm polished AI-rendered visual style, room composition, perspective, lighting, furniture, cup and keys on the coffee table, blue bag on lower-left chair, proportions and all other details. The elderly man should now be visibly slightly losing balance as if a possible stumble is starting: torso inclines forward and a little sideways, knees slightly bent, one foot lifting awkwardly while his other hand naturally reaches out for balance, his cane still held in the hand closest to the coffee table. A mild plausible pre-fall moment, upright but unstable; no actual fall onto the floor, no injury, no distress expression. Keep entire body and cane in approximately the same central-right location with feet fully visible. Keep anatomical natural proportions, same grey-haired man, dark green cardigan and beige trousers. No cartoon stylization, no photodocumentary style, no extra people. IMPORTANT: generate NO text, arrows, bounding boxes, highlights, UI, logos or timestamps; frontend code will add precise illustrative annotations later. Image remains landscape 3:2.
