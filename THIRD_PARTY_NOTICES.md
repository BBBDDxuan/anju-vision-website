# 随包第三方资源

## 模型

当前模型来源、版本和 SHA256 见 `elder-safety/weights/manifest.json`。
本次私有团队交付加入清单所列的两个实际 `.pt` 文件，不再只保留下载链接。
未启用的备选权重单独记于 `elder-safety/weights/alternative-manifest.json`。
原开发记录中的来源及许可信息保留于 `docs/` 和 `elder-safety/docs/`；相关 AGPLv3 文本见 `licenses/AGPL-3.0.txt`。

## 媒体工具

`elder-safety/tools/ffmpeg/` 包含原项目实际工作的 FFmpeg 与 FFprobe。
二进制版本、文件 SHA256、构建者及对应源码获取地址记录在该目录的 `provenance.json`。
FFmpeg 上游与 Gyan.dev 构建发行页提供源代码和相关构建信息；随包 GNU GPLv3 文本在 `licenses/GPL-3.0.txt`。
通过 imageio-ffmpeg 获取的工具还保留该包的原始许可于 `licenses/imageio-ffmpeg-LICENSE.txt`。

## 团队开发素材及凭据

按所有者要求不包含任何视频或视频分片。配置模板不含真实凭据；本机 `.env`、摄像头密码和访问令牌不提交 Git。
前端居家示意图仍为原有功能示意图，并非检测结果。
上述第三方许可文本不替代各来源的完整使用与分发条件。
