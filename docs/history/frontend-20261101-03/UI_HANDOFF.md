# 安居视界网页美化第一版交付

最新修订：参见 [第四版导航层级说明](UI_REVISION_4.md)。产品介绍下设四个二级导航，实时摄像头和导入视频为独立一级入口；以下内容保留第一版交付记录。

当前已按要求撤回第五版文案精简，恢复精简前的内容与布局，并保留侧栏底部两项贴底定位。后续文案删除按指定位置逐项调整。

当前第六版仅按指定修改摄像头页：删除标题下第二行功能小字，并适配桌面一屏显示，参见 [摄像头页一屏布局](UI_REVISION_6.md)。其他页面沿用精简前内容。

最新第七版进一步将预览框本身设为 16:9，实时状态与会话事件放在右侧，参见 [16:9 预览布局](UI_REVISION_7.md)。

日期：2026-11-02（北京时间）。开发目录：`hack_2026_AI/web-ui`。

## 已完成

- 深绿色导航与浅色内容区；统一中文字号、对比度、表单和按钮样式。
- 首页与工作台布局适配；增加相机、录像与事件、证据问答快捷入口。
- 保留 16:9 相机画面、会话事件、检测和存储折叠详情。
- 摄像头状态请求失败时收起旧画面，主指标停止展示旧 FPS / 人数 / 火焰候选，录像与传送状态显示待更新；仍可尝试停止已有会话。
- 预览图片请求失败独立显示实际错误，并自动重试；操作错误不会被成功轮询立即清掉。
- 手机导航抽屉支持焦点限制、Escape 关闭、焦点返回、背景滚动锁定；关闭的抽屉不进入键盘焦点顺序。
- 保留分析任务、暂停恢复、事件复核、证据和删除确认的原有接口。

## 修改范围

源码：`frontend/src/{main,Shell,LandingPage,LiveCameraPanel}.tsx`，新增 `frontend/src/theme.css`。

其他：`frontend/index.html`，`frontend/tests/LiveCameraPanel.test.mjs`，重新生成 `frontend/dist`。

包管理文件没有引入新的产品依赖。后端、模型规则、API 路径、相机轮询频率未改。原交接包 109 个文件的哈希复查无变化。

## 实际验证

| 项目 | 结果 | 证据 |
|---|---|---|
| node 前端测试 | 26/26 通过，原有24项及2项状态回归 | `review/node-tests.log` |
| TypeScript / Vite | 构建通过 | `review/build.log` |
| 响应式 | 首页和工作台各7种尺寸，14组无横向溢出 | `review/layout-results.json` |
| 尺寸 | 320×812、390×844、600×900、768×1024、1024×768、1280×720、1440×1000 | `review/after` |
| 浏览器状态与交互 | 35项通过；含6种状态、预览失败恢复、请求失败、开停POST、折叠详情、抽屉键盘操作与长文本 | `review/interaction-results.json` |
| 控制台 | 恢复真实空数据页面后0错误、0警告 | 浏览器当前页面检查 |

摄像头状态、录像中状态、会话事件和长文件名由 `review/interactions.js` 在浏览器请求层拦截产生。模拟页面会显示现有的测试模式横幅，相关截图名称均以 `mock-` 开头。脚本结束后移除所有拦截并返回真实空数据页面。没有操作真实相机、调用云模型、读取私人视频或写入检测结果。

截图已人工查看：桌面首页、手机首页、桌面工作台、手机工作台、手机抽屉和完整相机卡片。无模拟的截图反映当前未配置摄像头/模型的实际空数据状态。

1280×720 实际空状态下相机卡片约684px高。字号与状态反馈优先，未将前序661px作为必须保持的高度指标。宽度适配不等于 iOS/Android 真机验收；当前浏览器验证在本机 Chrome 中执行。

## 启动

开发副本目前的8000后端已启动。直接访问：

- 首页：`http://127.0.0.1:8000/`
- 工作台：`http://127.0.0.1:8000/workspace`

本机手动重新启动（先停止原服务，避免端口冲突）：

```powershell
Set-Location C:\Users\32256\PythonProjects\hack_2026_AI\web-ui
.\start.ps1 -Python .\.venv\Scripts\python.exe
```

前端修改后：

```powershell
node --test frontend/tests/*.test.mjs
npm run build --prefix frontend
```

Vite开发页：`npm run dev --prefix frontend`。相机开停仍需从8000同源页面验收。

## 浏览器验证复现

Playwright CLI安装在 `runtime/browser-tools`，不会成为产品依赖；3个独立技能安装在用户的Codex技能目录。

```powershell
.\runtime\browser-tools\node_modules\.bin\playwright-cli.cmd -s=anju-ui open http://127.0.0.1:8000/workspace
.\runtime\browser-tools\node_modules\.bin\playwright-cli.cmd -s=anju-ui --raw run-code --filename=review/capture.js
.\runtime\browser-tools\node_modules\.bin\playwright-cli.cmd -s=anju-ui --raw run-code --filename=review/interactions.js
```

在其他电脑上可安装 `@playwright/cli` 后使用同一脚本。不能将模拟测试结果作为实际识别、录像或通知效果。

## 已知限制与回交方式

- 尚未验证真实 RTSP、GPU推理、实际录像、云复核和手机通知；需要负责人提供联调环境。
- 后端使用本机现有 Python 包并在独立虚拟环境补充 python-multipart；不是全新安装且完全符合 requirements 锁定版本的环境。
- 本轮是现有功能的首版视觉与交互改造；保留既有历史页面结构，尚未进行跨浏览器完整可访问性审计。
- ZIP仅包含前端、构建、设计/交付文档、截图及验证记录。合并时以 `frontend/` 为范围，不覆盖负责人后端或真实配置。
- 构建包含带哈希的资产；集成时应替换整个 `frontend/dist`，避免旧文件残留。交接原件保持不变，可用于回退。
