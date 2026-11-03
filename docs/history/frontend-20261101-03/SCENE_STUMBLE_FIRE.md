当前图片：`frontend/public/assets/home-care-scene-water-reflection.png`。按用户“看着不像水”的反馈，改为更清晰的透明浅水洼，带窗户倒影、鞋边水纹和水滴。保留人物、家具、标注与右上角火情小图。通过内置 image_gen 编辑。

实际生成描述：

Precise localized correction of the liquid under the elderly man's planted front shoe. The current white crust-like shape looks like a stain or plastic, NOT water. REPLACE that patch entirely with an unmistakable shallow transparent WATER PUDDLE on the wooden floor. Make the puddle roughly twice the current visible area, a low broad irregular oval extending mostly to the RIGHT and FRONT of the front shoe, remaining on exposed wooden floor, not on the rug. To read clearly even as a small website image, show recognizable reflected pale blue-white window rectangles and thin window mullions in the wet surface, a partial darker reflection of the man's shoe and trouser leg, gently warped reflections, one or two subtle curved ripple lines near the shoe, and a few separate round water droplets. The wood grain remains visible beneath the transparent water. Edges should be THIN clear meniscus with only isolated small specular highlights, absolutely no continuous opaque white rim, no white frosting, no foam, no milk, no ice, no paint, no plastic sheet. A slight cool blue-grey reflected daylight on the puddle contrasts with warm dry wood, without turning into bright blue cartoon water. It is clear spilled water, not flooding. CRITICAL preserve the entire rest of this image: exact elderly man position/pose/scale, room, furniture, cane, keys, cup, blue bag, rug, warm softly idealized architectural illustration art style and lighting. Change ONLY the small floor zone around and ahead of the front shoe. Do not add a cup, any new objects, text, boxes, arrows, labels or people. Same landscape 3:2 image.

以下保留前序制作记录。

最新图片修订：按用户要求，在老人前脚附近的木地板上添加一小片反光水渍及少量水滴，表达可能滑倒的原因。当前首页引用 `frontend/public/assets/home-care-scene-wet-floor.png`，1536 × 1024。人物位置、姿态、火情小窗和前端标注沿用上一版。

生成方式：内置 image_gen。参考图片：`frontend/public/assets/home-care-scene-foreground.png`。

实际编辑描述：

Make a precise localized edit to this exact image: add a SMALL shallow spill of clear WATER on the WOODEN FLOOR immediately under and around the elderly man's planted front shoe, along the lower-right edge of the rug. The purpose is to make the cause of his existing loss of balance understandable. The wet patch should have a believable irregular thin outline, a few tiny adjacent droplets, a subtle light silvery reflection of the window light, and soft partial reflections of the nearby shoe and furniture. Keep it restrained but visibly recognizable as water at thumbnail size, not a giant puddle, not a mirror, no exaggerated blue tint or luminous glow. Water belongs only on the exposed wood floor around the shoe, not across the carpet. CRITICAL: keep the elderly man's exact position, scale, face, pose, clothing, hand placement and cane unchanged, and preserve the room, sofa, coffee table, keys, cup, blue bag, rug, camera perspective, lighting and warm softly idealized architectural illustration style. Do not add a spilled cup or any new objects. Change only this small floor area. Do not add any text, labels, borders, boxes, arrows, fire, or additional people. Same landscape 3:2 image, same dimensions.

以下保留前序制作记录。

最新修订：老人移到更靠前的下方区域，首页主图现为 `frontend/public/assets/home-care-scene-foreground.png`。火情小图中以橙红色框标出火焰，并显示“疑似明火示意”。桌面与手机六种宽度均检查了人物标签与火情小图之间的间距、火焰框位置和页面溢出。

源码核对：`elder-safety/scripts/web-camera-worker.py` 230-233 行对火焰候选绘制 BGR(0,80,255) 边框，转换为网页颜色即 #ff5000，并标记 FIRE CANDIDATE。`LiveCameraPanel.tsx` 将 fire_detected 显示为“疑似明火”，会话事件显示独立复核状态。本次只核对代码，没有启动真实摄像头或做真实火情测试。

## 前景位置编辑的实际描述（内置 image_gen）

Edit this exact image only by repositioning the elderly man and his cane toward the FOREGROUND, downwards in the image. Preserve the warm softly idealized architectural illustration art style, lighting, camera view, room, sofa, table, keys and cup on table, blue bag on lower-left chair, furniture locations and all other details. Keep the same clear near-fall posture: diagonally inclined torso, deeply bent knees, startled focused expression, one arm reaching to catch the table, the other extended for balance. Move his whole body approximately 12 percent of IMAGE HEIGHT DOWN toward the viewer and 3 percent of IMAGE WIDTH LEFT, maintaining approximately the same body size, so the top of his head is at about 38 percent of image height and his feet at 84 percent of image height. The cane moves with him. His reaching hand should now aim toward the FRONT-RIGHT edge of the coffee table, keeping the movement anatomically plausible. A clearly lower foreground position is essential to leave a visually generous quiet upper-right background region for a separate fire thumbnail added later. ENTIRE body and cane remain in the frame with a visible floor margin below both feet. Do not move the table, keys, blue bag or sofa. Do not add frames, boxes, labels, text, fire or a second person. Do not change into a cartoon or realistic documentary photo. Single landscape 3:2 image.

参考图片：`frontend/public/assets/home-care-scene-stumble.png`。

以下保留前序制作记录。

最新排版：按用户要求，核心能力页恢复三个图标；火情小图移到首页老人主图的右上角，以画中画方式展示。老人仍为主图，风险框和物品框保留。六种尺寸检查火情小图位于主图内部且不遮挡人物风险框，核心能力页已无图片。

# 老人失衡与独立火情示意图

生成方式：内置 image_gen，2026-11-02。

最终资源：
- `frontend/public/assets/home-care-scene-stumble.png`：首页主图，1536 × 1024。
- `frontend/public/assets/kitchen-fire-scene.png`：首页主图右上角的小图，1536 × 1024。

两图沿用原有柔和暖色居家画风。首页红框标注老人风险，蓝框标注钥匙，绿框标注包；框与标签由前端定位。火情以独立小窗叠放于首页主图右上角，场景内容不合成为同一事件。两图均明确标注 AI 示意，无真实检测或模型结果。

## 老人姿态编辑的实际描述

Edit this exact image. The user says the elderly man currently looks like he is simply taking a step, NOT losing balance. Make his posture UNMISTAKABLY a loss of balance, a near fall caught in progress, while preserving the exact warm softly idealized realistic architectural-render illustration style and this room. Keep sofa, table, keys, cup, bag, room layout, camera angle and lighting unchanged. Elderly man remains in the central-right area but now his body is diagonally pitching LEFT toward the coffee table: hips and supporting foot remain near their original position, upper torso leans sharply left-forward at approximately 40 degrees from upright, shoulders low and head clearly displaced left of hips, both knees deeply flexed with one knee buckling. His left hand reaches urgently down toward the near right edge of the coffee table, fingertips just above the table as if trying to catch himself; his other arm extends out behind him for balance. His face has a natural startled, concentrated expression, NOT smiling. The cane is angled and slipping from hand, not providing stable support; feet staggered with back heel raised, not a casual high step. Keep the ENTIRE body and all limbs and cane in frame, anatomically correct and plausibly balanced in this instant of a developing stumble. No impact, injury, blood or distress spectacle. The silhouette must communicate loss of balance even at thumbnail size and without any detection box. No text, labels, boxes, UI or arrows, those will be added in frontend. Single landscape 3:2 illustration, same elder, same green cardigan/beige pants, same original image style, no cartoon.

参考图：`frontend/public/assets/home-care-scene-highlight.png`。

## 厨房明火小图的实际描述

Create a separate small kitchen fire demonstration illustration, using the attached living-room image ONLY as an ART STYLE AND PALETTE reference, not as the same scene. Match its softly idealized polished architectural AI-rendered style, warm wood, cream walls, gentle daylight and subtle green details. Not documentary photography, not a cartoon or chibi. Scene: a modest household kitchen viewed at a slightly elevated angle. A shallow pan on a stovetop in the central area has clearly visible orange FLAMES rising above the pan rim, a small localized accidental pan fire, distinct from an ordinary blue burner flame. A faint wisp of grey smoke, enough to indicate a hazard without obscuring the scene. Keep flame and pan centered and large enough to recognize in a small website thumbnail. Simple clean countertop, plain tile backsplash, restrained surrounding details, no people. A single coherent landscape 3:2 image. No labels, text, border, detection boxes, timestamps, logos, explosions, charred destruction or dramatic fire engulfing the room. It illustrates the visible-flame detection capability in a home safety product; UI will mark it as an AI scene illustration.

风格参考图：`frontend/public/assets/home-care-scene.png`。

## 验证

- TypeScript/Vite 构建通过。
- 首页与核心能力页在 1440、1280、1024、768、390、320 宽度检查：图片加载、标注边界与横向溢出通过。
- 首页人物全身和物品标注可见，火情仅出现在家庭危险卡片，三个卡片标题在桌面端对齐。
- 1024 × 768 核心能力页可在内容区域滚动查看；其余检查符合现有桌面分页与手机自然滚动布局。
- 截图：`review/home-scene-highlight-1440.png`、`review/home-scene-highlight-390.png`、`review/fire-capability-1280.png`、`review/fire-capability-390.png`。
