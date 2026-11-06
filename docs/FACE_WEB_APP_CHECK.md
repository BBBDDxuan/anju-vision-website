# Web / App 人脸功能核对（2026-11-06）

网页和配套 `anju-vision-app/mobile-app` 源码已对齐同一组家庭成员与人脸接口。此记录对应本地未提交修改，不表示已发布或手机已更新。

| 功能 | Web | Flutter App |
|---|---|---|
| 共享档案增删改、样本数、revision | 已接入 | 已接入；冲突保留编辑，避免覆盖网页新数据 |
| 标签颜色、简称 | 可编辑 | 同步显示，编辑其他字段时保留 |
| 取得同意后录入 JPEG/PNG | 支持批量、部分成功提示 | 支持逐张录入，读取 Android 相册 content URI |
| 照片试识别 | 人脸框、编号、姓名或未知 | 人脸框、编号、姓名或未知 |
| 跌倒/求助身份候选 | 摄像头和录像告警 | 首页、事件列表、成员记录和事件详情 |
| 清除模板、删除成员 | 撤销旧告警身份 | 同步撤销；本机清除成功后立即撤下缓存身份 |
| 明火、歧义或缺少证据 | 不挂成员姓名 | 不以人脸匹配认定成员 |

手机 JPEG 常通过 EXIF 存储旋转或镜像。本次改为按照片显示方向解码后再缩放，保证模型输入和前端预览方向一致；覆盖全部 8 种 EXIF 方向。

验证结果：

- 157 项后端测试通过：档案、人脸、实时摄像头、统一告警、混合复核、静态页面。
- 50 项 AI 候选及跟踪证据集成测试通过。
- 网页 42 项单元测试、7 项 Edge/Playwright 浏览器测试通过，生产构建成功。浏览器测试包含移动尺寸、同意控制、批量部分失败、冲突、清除、删除和告警身份撤销。
- App 144 项 Flutter 测试通过，2 项依赖旧接口专用夹具的测试跳过。包含 320px / 1.8 倍字体的人脸操作、无本地路径的相册文件、试识别不录入、缓存身份撤销、跨端编辑冲突。
- App 通过真实 HTTP 调用临时手机网关，验证共享档案、录入、试识别、清除、删除。该夹具使用合成特征，不代表真实人脸识别准确率。
- Flutter 分析无错误和警告，仅两条原有 Firebase 弃用 info；Android debug Dart bundle 编译成功。
- 两个人脸模型 SHA-256 校验成功，真实模型加载及合成空白 JPEG/PNG 推理通过。

本次没有生成/签名新 APK、安装到手机，也没有用真实家庭照片或摄像头测识别准确率。现有 Android SDK 路径仅有 platform-tools，缺少完整 APK 构建环境。App 的原生照片选择、真实设备预览和长时间监护仍需新包安装后实测；iOS 未构建。后端和 App 需要配套更新，已有手机上的旧 APK 不会因源码修改自动获得功能。

本地复现入口：website 的 `tests/test_face_recognition.py`、`tests/test_member_profiles.py`、`frontend/tests/members.spec.ts`；App 的 `test/member_profiles_test.dart`、`test/phone_contract_http_test.dart` 与 `test/support/phone_contract_fixture.py`。
