# 网页家庭成员与人脸识别

在本机启动或重启后端，访问 `http://127.0.0.1:8000/workspace?view=members`，也可以从工作台左侧“家庭成员”进入。

1. 添加成员，填写姓名、关系、备注并保存。可选颜色和简称便于区分列表。
2. 在“人脸资料”确认已取得该成员同意，选择 JPEG/PNG 照片录入。建议正脸、稍向左和向右共 3–5 张；支持批量选择，单张不超过 5 MiB、1600 万像素，每人最多 10 张。批量录入遇到失败会停止，保留并显示前面成功的数量。
3. 选择另一张照片“试识别”，查看人脸位置和各自的身份候选。试识别比对全部已录入成员，不会增加样本。
4. 进入实时摄像头开启监护，或导入录像执行安全分析。经危险复核确认后，系统尝试把人物证据与已录入人脸匹配。在“本次会话事件”或录像“预警与通知设置”中显示姓名和“人脸识别候选，请核对”。多人重叠、人脸不可见或证据不足会显示“身份未确认”。明火等环境事件不归给某位成员。

人脸识别在电脑本地运行，录入原照不保存到后端，保存的是特征模板。原有危险复核仍可能向云模型发送事件证据画面，姓名和人脸模板不加入云端复核请求。试识别图片仅在当前页面预览，收起或离开页面释放预览。

清除人脸资料会保留成员档案和安全事件，撤销旧身份关联；删除成员同时清除该成员模板。档案编辑采用 revision 检查，发生并发冲突时保留当前编辑，用户可确认放弃后载入最新档案。颜色与简称是可选字段，旧客户端不传时保留已有值。

配套 App 已接入相同档案与人脸 API，在“成员监护 → 成员详情”录入、试识别和清除。App 同步网页标签颜色与简称，跌倒和求助的列表与详情展示身份候选。手机 JPEG 的 EXIF 旋转/镜像由后端校正；两端都仅接受 JPEG/PNG，HEIC 需先转换。完整核对记录见 [FACE_WEB_APP_CHECK.md](FACE_WEB_APP_CHECK.md)。

## 模型与运行

默认本地启用 `FACE_RECOGNITION_ENABLED=true`，云部署不开放家庭档案和人脸 API。缺模型时仍可管理档案；准备模型后点击“刷新列表”。

```powershell
.\.venv\Scripts\python.exe scripts/prepare_face_models.py --check
# 缺失时下载固定版本，再刷新网页：
.\.venv\Scripts\python.exe scripts/prepare_face_models.py
```

接口与模型限制见 [MEMBER_FACE_RECOGNITION.md](MEMBER_FACE_RECOGNITION.md) 和 [FACE_RECOGNITION.md](FACE_RECOGNITION.md)。

页面结构与颜色/简称字段参考 `origin/codex/manual-member-labels`（306ee96、6af840d）。该分支采用人工 track 标签，并强制关闭自动识脸；本轮只复用其成员页面基础，保留主线自动人脸匹配，没有引入该分支的停用逻辑或人工跟踪系统。

## 验证

```powershell
cd frontend
npm run build
npm test
# 先在另一个终端运行 npm run dev -- --port 5178 --strictPort
npx playwright test --config playwright.members.config.ts
```

浏览器测试使用模拟接口和合成小图，覆盖建档、原始图片 body、同意控制、批量部分失败、试识别、清除、删除、版本冲突、模型缺失、移动尺寸和两类告警身份。真实模型通过后端测试加载并处理合成空白图；这些检查不等同于真实家庭摄像头识别准确率验收。
