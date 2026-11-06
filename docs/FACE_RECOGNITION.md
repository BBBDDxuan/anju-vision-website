# website 本地家庭成员人脸识别

本功能在 website 后端使用 YuNet 检测、五点对齐和 SFace 特征比对。新增照片录入、识别、模板删除接口，并为已确认的跌倒/求助告警补充家庭成员身份。App 的配套共享档案与照片录入流程见 docs/MEMBER_FACE_RECOGNITION.md。

## 启用和模型

默认在本地后端启用，云部署强制关闭。重启 website 后端后生效；不需要另启一个人脸服务。现有 requirements.txt 已包含 OpenCV 和 NumPy，本次没有新增 Python 依赖。

| 环境变量 | 默认值 | 含义 |
|---|---|---|
| FACE_RECOGNITION_ENABLED | true | 是否允许录入和推理 |
| FACE_MODEL_DIR | models/face-recognition | 相对于 website 根目录的模型目录 |
| FACE_MATCH_THRESHOLD | 0.363 | SFace 余弦相似度最低值 |
| FACE_MATCH_MARGIN | 0.08 | 第一名与第二名家庭成员的最低分差 |
| FACE_MIN_PIXELS | 32 | 推理图像中人脸宽高的最小值 |
| FACE_MAX_SIDE | 1280 | 送入检测前图像最长边上限 |

大于 640 像素的图像在首次检测未找到人脸时，会缩至最长边 640 像素再检测一次，以处理大幅近景人脸；不会因此放宽录入人数或匹配阈值。

模型为 face_detection_yunet_2026may.onnx 和 face_recognition_sface_2021dec.onnx，合计约 37.1 MiB。每次进程首次加载时核对固定 SHA-256；缺失、损坏或 OpenCV 不支持时返回明确错误，不自动下载。目录中的 manifest.json 保存来源和固定上游版本，licenses/face-recognition 保存上游许可与说明。

两份人脸权重在当前电脑已准备好，并由 Git 忽略。新电脑拉取仓库后需先运行准备脚本。在 website 根目录可检查模型，缺失时可显式下载固定版本：

~~~powershell
./.venv/Scripts/python.exe ./scripts/prepare_face_models.py --check
# 需要恢复模型时才执行，会联网下载并校验：
./.venv/Scripts/python.exe ./scripts/prepare_face_models.py
~~~

## HTTP 接口

本机回环地址可访问；局域网必须使用项目现有手机入口的 Bearer 令牌。云部署不开放这些接口。返回值带 Cache-Control: no-store。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | /api/faces/status | 开关、模型就绪情况、阈值和限制 |
| GET | /api/faces/members | 已录入模板的成员与样本数，不返回特征向量 |
| POST | /api/faces/members/{member_id}/samples?name=姓名&consent=true | 追加一个模板 |
| POST | /api/faces/recognize | 识别图片内各个人脸 |
| DELETE | /api/faces/members/{member_id}/samples | 删除该成员全部人脸模板 |
| DELETE | /api/faces/members/{member_id} | 上一接口的兼容别名，仅删除模板 |

POST 请求直接以 JPEG/PNG 字节作为 body，Content-Type 为 image/jpeg 或 image/png，不使用 multipart。图片上限 5 MiB、1600 万像素；录入要求恰好一张尺寸合适的人脸。每个家庭最多 32 名成员，每名成员最多 10 个样本。成员 ID 为 1–80 个 ASCII 字母、数字、下划线或短横线；姓名最多 80 字符。录入前须实际取得被录入者的单独同意，consent=true 是客户端确认，不能代替真实同意。

~~~powershell
curl.exe http://127.0.0.1:8000/api/faces/status
# 使用本人同意的照片，替换 photo.jpg；可先用英文名避免终端 URL 编码差异。
curl.exe -X POST 'http://127.0.0.1:8000/api/faces/members/member-1/samples?name=Member1&consent=true' -H 'Content-Type: image/jpeg' --data-binary '@photo.jpg'
curl.exe -X POST 'http://127.0.0.1:8000/api/faces/recognize' -H 'Content-Type: image/jpeg' --data-binary '@another-photo.jpg'
curl.exe -X DELETE 'http://127.0.0.1:8000/api/faces/members/member-1/samples'
~~~

识别返回 faces、face_count、model、threshold；每张脸带归一化 bbox、检测分数及 matched/unknown 状态。matched 时含 member_id、name、revision、similarity；unknown 时 member_id 为 null，并给出原因。余弦相似度不是置信概率。多成员分差不足、人脸太小或低于阈值时保持 unknown，不推断“入侵者”。

家庭档案接口与模板分开：GET /api/household/members、PUT /api/household/members/{id}、DELETE /api/household/members/{id}。清除人脸模板保留姓名、关系等档案；删除整个档案会同时删除模板。关闭 FACE_RECOGNITION_ENABLED 后，本地家庭档案仍可用，但录入/推理被禁止。

## 告警关联

危险候选仍先由现有小模型和 Qwen 视觉复核流程确认。确认后才在本地尝试身份关联，结果不用于取消危险告警，也不会把姓名或人脸特征加入云端复核请求。原有事件证据帧仍按原流程发送至复核服务，本功能不会将这部分流程变成本地离线检测。

候选需要带逐帧 person_bboxes。每张脸必须唯一落在候选 track_id 对应的人物框内，至少两个不同时刻匹配同一成员；人物框重叠、身份冲突、证据不足或推理失败时返回 unknown。实时采集与上传录像扫描都保留该元数据。旧候选只有触发时的 subject_bbox_hint 时不分配身份；历史缓存不会自动重算。

统一告警与录像告警响应增加 member_id 和 face_identity。严格记忆 JSON 的实体/事件结构保持原样。删除模板后，读取历史身份关联返回 unknown，不会因同 ID 重新录入而恢复旧关联。已发送到外部的消息或客户端缓存不由本地删除接口追溯清除。

## 存储与验证

身份模板保存在 settings.data_dir/face-identities.sqlite3，默认 data/face-identities.sqlite3，与家庭档案共享数据库。录入照片只在内存中处理，不写入人脸照片库；原有摄像头录像和事件证据仍遵循原保存策略。128 维特征模板仍属于敏感身份数据，SQLite 文件未做应用层加密，应限制本机账户访问并纳入实际部署的数据管理。

可复现公开样本接口测试：

~~~powershell
./.venv/Scripts/python.exe ./scripts/smoke_face_recognition.py --samples '公开样本目录'
~~~

样本目录需要 david1.jpg、david2.jpg、100032540_1.jpg、100040721_1.jpg。本地已有样本位于外层 evaluation/face-recognition-yunet-sface/samples，未复制到产品仓库。脚本使用真实模型和后端 HTTP 路由，在全新的 runtime/face-smoke-* 数据目录中测试录入、同人识别、陌生人、空白图和删除撤销；结束时删除测试档案及模板，保留 JSON 报告。候选关联部分使用明确标注的合成人物框，不代表真实人体跟踪验收；不启动摄像头或调用付费接口。

0.363 阈值与 0.08 分差尚未针对实际家庭摄像头校准。本次未验证真实老人、RTSP 连续流、遮挡、远距离、跨日变化或活体检测；照片/屏幕重放防护不在当前实现范围内。配套 App 已提供成员档案和照片录入界面；本轮尚未进行真机操作验收，也未验证家庭场景识别准确率。

## 本次接入验证（2026-11-05）

| 验证 | 实际结果 |
|---|---|
| 后端人脸、共享档案、联系人分级提醒、告警、推送、证据、手机访问与摄像头相关回归 | 239 项通过，0 失败 |
| AI 服务危险候选与逐帧人物框关联测试 | 44 项通过，0 失败 |
| 两份模型大小及 SHA-256 校验 | 通过 |
| 真实 OpenCV 模型加载与合成空白图片检查 | PNG/JPEG 均返回 0 张人脸；空白图录入被 FACE_ENROLLMENT_QUALITY 拒绝；隔离数据库没有遗留成员或模板 |
| 配套 App 全套 Flutter 测试 | 135 项通过，4 项独立后端夹具测试跳过 |

本轮未使用家庭照片或真实人物样本测量准确率，未连接真实摄像头或调用付费服务，未执行 APK 构建与真机验收。上面的模型检查只证明本机模型能加载并处理空白图片，不证明家庭成员识别准确率。现有 Starlette/httpx TestClient 弃用提示仍存在。

本机验证日志保存在仓库外层的 整理记录/2026-11-05/成员监护与人脸识别/，包括 backend-final.log、candidates-final.log、real-model-check.log 与 flutter-final-complete.log；日志与临时数据库不上传 GitHub。

Windows 回归测试应使用短的 ASCII 临时路径，避免现有 OpenCV 文件路径接口不支持中文路径，以及长证据文件名触发路径长度限制。例如在 website 根目录执行：

~~~powershell
$env:PYTHONUTF8 = '1'
$env:OPENCV_FOR_THREADS_NUM = '4'
$env:PATH = (Join-Path $PWD 'elder-safety/tools/ffmpeg') + ';' + $env:PATH
$testRoot = Join-Path $env:TEMP ('af-' + [guid]::NewGuid().ToString('N').Substring(0,8))
./.venv/Scripts/python.exe -m pytest tests -q --basetemp $testRoot
~~~
