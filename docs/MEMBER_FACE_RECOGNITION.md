# 家庭成员档案同步与本地人脸识别

网页入口与操作步骤见 [网页家庭成员与人脸识别](HOUSEHOLD_MEMBERS.md)。档案可选 `label_color`（#RRGGBB）、`label_letter`（最多2字符）；不传时保留已有样式，移除成员后同时清除样式。

服务端维护一个本地家庭的权威档案；客户端按后端地址/家庭隔离缓存。手机旧档案留在本地，仅在用户明确选择导入后上传，导入时生成新随机 ID，避免不同设备的旧 member-1 等编号冲突。后端原样保留请求中的稳定 ID，不自动导入手机数据。

## 配置与模型准备

在 website 根目录 PowerShell 执行：

```powershell
# 仅检查已有文件，不联网
& .\.venv\Scripts\python.exe scripts/prepare_face_models.py --check
# 用户显式执行才下载；总计38,926,091 bytes，约38.9 MB / 37.1 MiB
& .\.venv\Scripts\python.exe scripts/prepare_face_models.py
# 自定义目录必须与 FACE_MODEL_DIR 一致
& .\.venv\Scripts\python.exe scripts/prepare_face_models.py --model-dir G:\models\face-recognition

# 只同步档案，不启用人脸
$env:FACE_RECOGNITION_ENABLED = 'false'
# 取得单独同意、准备模型后，再显式启用并重启服务
$env:FACE_RECOGNITION_ENABLED = 'true'
$env:FACE_MODEL_DIR = 'models/face-recognition'
```

Settings.from_env() 仅把大小写无关的 true 视为启用。当前 config.py 默认值、from_env 回退值与 .env.example 均为 true。Cloud 模式始终拒绝私有家庭 API。关闭 face 仅禁用录入/识别，档案 CRUD 与清模板仍可用；缺模型也不影响档案 CRUD。

脚本仅用 Python 标准库；导入模块、服务启动或请求 status 均不自动下载。固定官方 OpenCV Zoo commit 为 `48b10fa505aeb51fbd127fc60a5c660040e823b7`；固定 URL 见 scripts/prepare_face_models.py 的 MODEL_SPECS。每个模型写同目录临时文件，校验固定长度/SHA-256 后 os.replace 原子安装；失败保留旧文件并清除本次临时文件。正确文件跳过下载。两个模型并非作为一组原子替换。

| 模型文件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| face_detection_yunet_2026may.onnx | 229738 | ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0 |
| face_recognition_sface_2021dec.onnx | 38696353 | 0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79 |

许可证见 licenses/face-recognition/。当前 .gitignore 忽略人脸 ONNX 权重，由准备脚本下载固定版本；录入照片、模板和运行数据同样不提交。下载脚本不操作 Git。完整后端接入与真实样本验证见 [FACE_RECOGNITION.md](FACE_RECOGNITION.md)。

## 授权、挂载与响应

全部路径经既有 face_router(service, settings) 一次挂载；main 复用 manager.safety.faces，API/Hybrid 使用同一实例。LAN 沿用 Authorization: Bearer <APP_LAN_TOKEN>；不配 token 时仅传输层 loopback 可访问。X-Forwarded-For 不用于授权。配有 token 的未授权客户端返回401；未配 token 的非本机返回403；Cloud 模式返回403。成功 JSON 响应 Cache-Control: no-store。

main 的 CORS allow_methods 已包含 PUT，允许既有本地网站来源调用档案更新接口；原生 Flutter HTTP 不受浏览器 CORS 限制。

## 档案 API 契约

GET /api/household/members → 200：

```json
{"members":[{"id":"phone-random-123","number":1,"name":"合成测试成员","relationship":"家人","notes":"","bindings":[{"device_id":"camera-1","scope":"phone-local","track_id":""}],"face_sample_count":0,"revision":1}]}
```

空列表为 {"members":[]}；按 number、id 排序。无列表总 revision，使用逐成员 revision。

PUT /api/household/members/{id}，Content-Type: application/json，完整请求体示例：

```json
{"id":"phone-random-123","number":1,"name":"合成测试成员","relationship":"家人","notes":"","bindings":[{"device_id":"camera-1","scope":"phone-local","track_id":""}],"face_sample_count":0,"revision":1}
```

新建或更新均返回200 {"member":{...完整档案，face_sample_count，revision}}。替换全部可写字段，不执行部分合并。id 必须匹配路径；未知额外字段拒绝。接受 Flutter 完整 toJson 的 face_sample_count，但忽略客户端计数，始终从模板表计算。

| 字段 | 类型/约束 |
| --- | --- |
| id | 必填字符串，1–80位ASCII字母、数字、_、-；保留稳定 ID |
| number | 必填严格整数，1–2147483647；bool、浮点和字符串拒绝；只是显示编号，不要求唯一 |
| name | 必填字符串，0–80字符；允许空姓名，识别显示名回退“成员N” |
| relationship | 必填字符串，0–80字符 |
| notes | 必填字符串，0–2000字符；允许换行/回车/制表符 |
| bindings | 必填数组，最多32项；同一档案内的完整绑定三元组不重复 |
| bindings[].device_id | 必填非空字符串，1–128字符 |
| bindings[].scope | 必填非空字符串，1–512字符；可保存服务地址或 phone-local |
| bindings[].track_id | 可省略，默认空字符串，最多128字符；不是已验证的人脸身份 |
| face_sample_count | 可省略/null，否则非负严格整数；只读回传，服务端忽略 |
| revision | 可省略/null，否则非负严格整数；响应为正整数 |

除 notes 的上述空白字符外，拒绝 ASCII 控制字符。请求体最多64 KiB。一个服务最多32个家庭档案，包括未录脸的档案。

### 版本检查与删除

- PUT 不传 revision 或传 null：在事务内完整覆盖。
- PUT revision=0：仅当前 ID 不存在时创建，存在则409。
- PUT revision=N（正整数）：仅当前版本等于 N 时更新，缺失或过期则409。
- DELETE /api/household/members/{id}?revision=N：可选版本检查；不传为无条件删除。不存在按版本0判断；携带旧正版本重复删除会409。
- 删除返回200 {"member_id":"...","deleted":true}，原子删除档案、全部模型的模板，撤销旧候选身份。无条件重复删除为 deleted=false。
- revision 在每次完整 PUT、成功录入样本、对现存档案清除模板时递增（即使样本数为0，也取消进行中的首次录入）。删除保留仅含 ID/版本的 tombstone；重建不复用旧版本，不恢复旧候选身份。
- 样本操作后重新 GET 档案，获取最新 revision。冲突为409 HOUSEHOLD_REVISION_CONFLICT；客户端刷新服务端记录，保留未保存编辑供用户处理。

## 人脸 API 契约

| 路由 | 行为 |
| --- | --- |
| GET /api/faces/status | 200；enabled、ready、model、threshold、margin、min_face_pixels、local_only=true、stores_source_photos=false、max_image_bytes=5242880、max_image_pixels=16000000、max_members=32、max_samples_per_member=10、max_bindings_per_member=32、profiles_available、member_count；缺模型仍200、ready=false、error=FACE_MODEL_MISSING |
| GET /api/faces/members | {"members":[{"id":"...","name":"...","consent_at":"UTC ISO8601","sample_count":1}]}；只列 face 成员记录，关闭 face 仍允许管理读取 |
| POST /api/faces/members/{id}/samples?name=姓名&consent=true | raw JPEG/PNG，Content-Type image/jpeg 或 image/png，非multipart，≤5MiB且≤1600万像素；201 {"member":{"id":"...","name":"...","consent_at":"...","sample_count":1}} |
| DELETE /api/faces/members/{id}/samples | 200 {"member_id":"...","deleted":true,"removed_sample_count":2}；清所有模型的模板，撤销旧身份，保留档案/绑定；无模板 deleted=false；关闭 face 仍可用 |
| DELETE /api/faces/members/{id} | 兼容旧接口，同样仅清模板，返回形状同上 |
| POST /api/faces/recognize | 同样raw JPEG/PNG；200 {"faces":[...],"face_count":1,"model":"yunet-2026may+sface-2021dec","threshold":0.363,"similarity_is_probability":false}；无脸时 faces=[]、face_count=0 |

录入须取得被录入者单独同意；缺失同意或不是恰好一张足够清晰、大小合适的脸时拒绝。最多10样本/成员。已有档案姓名权威，录入 name 参数不会覆盖改名。兼容旧调用：ID 不存在时可原子创建最小档案，number=当前最大编号+1。录入推理期间档案修改/删除会409，防止复活。

匹配脸包含 status=matched、member_id、当前 name、revision、similarity、margin、reason、model、bbox（归一化xyxy）、detection_score、quality。unknown 的 member_id=null，不得由客户端根据相似度升级为确定身份。status ready 只表示模型加载成功，不表示任何个体的准确率。

## 错误映射

格式：{"error":{"code":"...","message":"中文提示","retryable":false}}。按 code 映射，不依赖文案。status 的模型错误以200响应中的 error 字段表达。

| HTTP | code |
| --- | --- |
| 401 | PHONE_AUTH_REQUIRED |
| 403 | FACE_RECOGNITION_DISABLED、FACE_LOCAL_ONLY |
| 409 | HOUSEHOLD_REVISION_CONFLICT、HOUSEHOLD_MEMBER_LIMIT、FACE_MEMBER_LIMIT、FACE_SAMPLE_LIMIT |
| 413 | HOUSEHOLD_BODY_TOO_LARGE、FACE_IMAGE_TOO_LARGE |
| 415 | HOUSEHOLD_CONTENT_TYPE、FACE_IMAGE_TYPE |
| 422 | HOUSEHOLD_PROFILE_INVALID、HOUSEHOLD_MEMBER_INVALID、HOUSEHOLD_ID_MISMATCH、FACE_CONSENT_REQUIRED、FACE_MEMBER_INVALID、FACE_ENROLLMENT_QUALITY、FACE_IMAGE_INVALID、FACE_IMAGE_DIMENSIONS、FACE_TOO_MANY_FACES、INVALID_REQUEST |
| 503 | FACE_MODEL_MISSING、FACE_MODEL_INTEGRITY、FACE_MODEL_RUNTIME、FACE_FEATURE_INVALID、FACE_INFERENCE_FAILED、FACE_STORAGE_UNSAFE |

正常 unknown reason 包括 disabled、face_too_small、no_enrolled_members、below_threshold、ambiguous_match、not_person_event、insufficient_tracked_face_evidence、conflicting_frames、conflicting_candidates、identity_not_analyzed、identity_unavailable、member_deleted。候选需至少两个不同时间帧，在各帧中唯一落入同一人的跟踪框，且身份一致；无法确定时保持 unknown。身份失败不改变危险确认，不阻塞告警。历史候选读取当前档案姓名；删除后不会因同 ID 再录入恢复匹配。

## 私有存储与验证边界

私有文件是 settings.data_dir/face-identities.sqlite3，不是模型目录或单独 face_data 文件夹。表为 household_members、household_versions、既有 members/samples/associations。旧 face 成员打开存储时迁移成档案，稳定 ID/姓名/样本保留；不等于上传手机旧档案。

使用 SQLite 外键、secure_delete、BEGIN IMMEDIATE 和线程锁，跨实例/线程序列化版本检查及写入。原始录入照片仅在内存解码，不新增照片文件、不通过API返回embedding；存储128维归一化特征。原有安全证据图片属于既有证据链，本功能不新建或删除。拒绝存储文件/sidecar及父目录的符号链接或Windows junction，拒绝非普通文件/硬链接。新目录0700、新DB0600（POSIX）；Windows沿用目录ACL，未声称重设Windows权限。数据库及备份属于敏感数据，勿提交/上传。

验证只用mock特征、合成图片和本地已有模型，不使用真实个人照片、摄像头或付费服务。模型下载测试以BytesIO/mock替代网络。真实模型检查SHA-256、OpenCV加载，以及全黑PNG/JPEG无脸结果和录入拒绝；不证明任何个人识别准确率，不包含活体检测或防翻拍保证。

```powershell
New-Item -ItemType Directory -Path .tmp -Force | Out-Null
& .\.venv\Scripts\python.exe -m pytest tests/test_face_recognition.py tests/test_member_profiles.py -q --basetemp .tmp/face-profile-check
& .\.venv\Scripts\python.exe scripts/prepare_face_models.py --check
```
