# 本地 Android APK 更新

仅通过现有本地 phone gateway 提供更新，不需要域名、云存储、新令牌或源码 ZIP。首次仍由维护者手动分发并安装正式签名 APK；后续 App 使用已经配置的同一后端 origin。

## 运行边界

- 路由仅注册在 `backend.phone_app.create_app`；普通 `backend.main` 和 cloud_mode 不注册。phone gateway 本身拒绝 cloud_mode。
- 复用主服务的 LAN Bearer 鉴权与现有 APP_LAN_TOKEN。LAN 请求必须携带 `Authorization: Bearer <现有令牌>`；loopback 保留现有本机访问策略。发布脚本不需要令牌、不创建凭据。
- 沿用 `start-phone.ps1 -Address <本机局域网IPv4>`。电脑和后端必须运行，手机必须能访问该 origin。公网网站、电脑关机或离开可达网络时不提供本地更新。发布后无需重启已经运行的新版后端。
- 私有目录为 `runtime/app-updates/`，包含 manifest、公开证书 pin 和 `<sha256>.apk`；该目录从 Git 排除且不挂载为静态目录。另忽略 `*.apk`，防止误提交。
- 单 APK 大小为 1..268435456 字节（256 MiB）；notes 最多 10000 字符，version_name 最多 100 字符。只接受 `com.eldersafety.elder_safety_mobile`，拒绝 `.test`、debuggable 和 Android Debug 签名包。

## 工具状态与签名 pin

不构建 APK，不运行 Flutter/Gradle，不安装 SDK/JDK/大依赖，不调用模型或上传文件。使用网站现有 Python 与已有 aapt2/apksigner。推荐直接传 apksigner.jar 和 Java。

2026-11-05，App 的 `mobile-app/tool/android-env.ps1` 配置预期工具根目录为 `G:\比赛\AI黑客松\手机端\C模块\tools`，但当前该根目录下 SDK/JDK 缺失。以下是与该配置对应的预期路径，**不是已验证存在的工具**：

```powershell
$tools = 'G:\比赛\AI黑客松\手机端\C模块\tools'
$sdk = Join-Path $tools 'sdk'
$java = Join-Path $tools 'jdk\bin\java.exe'
Test-Path -LiteralPath $sdk
Test-Path -LiteralPath $java
```

当前先完成源码和测试集成；待发布环境已有工具及新 APK 后再发布。无需为源码提交构建 APK。工具缺失时脚本明确失败，不猜版本、不生成假 manifest。

可以指定 `--sdk <已有SDK根目录>`，或设置 ANDROID_SDK_ROOT / ANDROID_HOME；脚本从 build-tools 选择具备 aapt2 和 lib/apksigner.jar 的最高稳定数字版本。Java 来自 `--java <java.exe绝对路径>`、JAVA_HOME 或 PATH。支持任意已有盘符，无需在 C 盘安装。

也支持独立工具参数 `--aapt <aapt2.exe绝对路径>` 和 `--apksigner <apksigner.jar或可执行文件绝对路径>`，两者须一起指定。JAR 需要 Java；使用 apksigner.bat 时其 Java 环境由已有 JAVA_HOME/PATH 提供。

首次默认使用原正式签名公开 SHA-256，沿用现有 App `mobile-app/tool/build-c.ps1` 的可信原 APK 签名校验策略。以下公开指纹来自现有 App `docs/b-c-integration/RESULTS.json` 的历史正式构建记录；该构建脚本通过 `-ExpectedSigningSha256` 接收指纹，不在源码中硬编码此值，尚未在本机用新 APK 复核：

```text
2472d43f63dbea0d2d3c34933114caf3dc532055c97ceaa40e96a968070b3e35
```

可用 `--expected-cert-sha256` 或进程环境变量 APP_UPDATE_CERT_SHA256 显式指定可信摘要（允许冒号和大小写）。已有发布时默认沿用 manifest pin，显式配置也必须匹配已发布 pin。所有输入先运行 `apksigner verify --verbose --print-certs`，要求恰好一个当前 signer，再比较摘要。不读 keystore、密码或私钥，不自动接受新签名，不提供跳过验签开关。指纹是公开摘要，不是新凭据。

## 发布命令

以下占位工具/APK 路径必须替换为发布环境中实际存在的文件。三个 APK 应为同一 versionName 的三种 split-per-ABI 正式包。命令没有下载、构建或上传步骤。

```powershell
Set-Location -LiteralPath 'G:\比赛\AI黑客松\GitHub同步\website'
# 可选显式固定原正式证书；首次不设置也会使用这个默认 pin。
$env:APP_UPDATE_CERT_SHA256 = '2472d43f63dbea0d2d3c34933114caf3dc532055c97ceaa40e96a968070b3e35'
.\.venv\Scripts\python.exe scripts\publish_app_update.py `
  --aapt '<已有aapt2.exe的绝对路径>' `
  --apksigner '<已有apksigner.jar的绝对路径>' `
  --java '<已有java.exe的绝对路径>' `
  --apk '<新版本arm64-v8a.apk的绝对路径>' `
  --apk '<新版本armeabi-v7a.apk的绝对路径>' `
  --apk '<新版本x86_64.apk的绝对路径>' `
  --notes '1.2.4：支持本地应用更新。'
if ($LASTEXITCODE -ne 0) { throw '发布失败，检查工具路径、签名和实际 APK。' }
```

完整 SDK 可用 `--sdk $sdk` 替换两个独立工具参数，并保留 `--java $java`。长说明可用 `--notes-file '<UTF-8文本绝对路径>'` 替代 --notes，二者只能选一个；空说明可用空文件表达。不自动加载 .env 的发布配置，环境变量应由发布进程明确设置。

`1.2.4+4009` 是 App 项目版本，不能手写成发布 version_code：分架构实际 code 可能具有 ABI 偏移。脚本通过 aapt2 badging 和二进制 AndroidManifest.xml 读取生产包名、versionName、versionCode/versionCodeMajor，合成为 Android longVersionCode；ABI 同时核对 badging 与 ZIP 中实际 lib/<abi>/*.so。不信任 BUILD-METADATA.json、文件名或任意命令行版本值。universal APK 按实际 ABI 建多条记录；同一批的 APK 不得重复覆盖同一 ABI。

## 原子发布及不可变文件

排他 .publish.lock 防止并发发布。先复制全部输入到私有临时区，检查大小和拷贝期间变化，检查副本的真实元数据、签名并计算 SHA-256；全部通过后才生成内容寻址文件，最后用 os.replace 原子切换 manifest。Windows 采用同盘、拒绝覆盖目标的 rename，兼容本机不支持硬链接的 G 盘；其他平台采用不覆盖目标的 hard link。

同一 ABI/code 不允许替换内容，同一 ABI 不允许降级；完全相同内容可重发说明。旧记录与旧 APK 保留，旧 digest URL 继续有效。已存在 digest 文件也必须校验，不覆盖损坏文件。历史上限为 4096 条，manifest 最多 1 MiB，达到限制拒绝发布并等待维护者规划保留策略。

失败不会把半批条目写入 manifest。切换前中断可能留下未引用内容文件，下载 API 不会提供它们。强制终止可能留下锁；确认没有发布进程后才手动清理 .publish.lock。不要手工编辑 manifest 或改写已发布 APK；目录只供可信本机维护进程写入。

## API 契约

请求使用已安装 APK 的实际 longVersionCode 和客户端选出的第一个支持 ABI：

```http
GET /api/app/version?version_code=4008&abi=arm64-v8a
Authorization: Bearer <现有令牌>
```

没有 manifest、ABI 未发布，或最新 code 不高于已安装值时，返回 200：

```json
{"available": false, "update": null}
```

有适配且更新的 APK 时，update 严格含这些字段（摘要和数值仅示意）：

```json
{
  "available": true,
  "update": {
    "version_name": "1.2.4",
    "version_code": 6009,
    "abi": "arm64-v8a",
    "package_name": "com.eldersafety.elder_safety_mobile",
    "size_bytes": 12345678,
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "download_path": "/api/app/apk/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "notes": "本地更新"
  }
}
```

示例 6009 不代表尚未构建产物的实际 code。仅接受 arm64-v8a / armeabi-v7a / x86_64。缺少或无效参数返回 422；LAN 未授权返回 401 和 WWW-Authenticate: Bearer。畸形、超限、重复键或不可信 manifest 返回 503，不伪装成“没有新版”。只有对应最新文件经过实际校验才返回 available:true；文件损坏时版本接口返回 503。

`GET /api/app/apk/<sha256>` 只允许已登记的小写 64hex digest，不接受文件名或用户目录。拒绝路径及祖先中的 symlink/junction/reparse point；检查普通文件、实际长度、完整 SHA-256，从同一个已校验文件句柄发送。没有 hash 缓存。Range 请求仍返回完整 200 APK。响应带真实 Content-Length、APK MIME、附件名、Cache-Control: private, no-store；版本检查同样禁止缓存。未知/缺失/长度错误/内容损坏 APK 下载返回 404；manifest 损坏返回 503。私有运行目录没有静态 URL。

## 手机端与验收

客户端仍需限制 256 MiB，验证完整长度、SHA-256、包名、实际 build code、当前已安装签名证书，限制同 origin 下载，然后交由系统确认安装。可能需要用户允许该 App 发起安装；后端不静默安装或绕过确认。首次手动安装也须用原正式签名和包名；不同签名、卸载重装、独立 .test 包不属于原位更新验收。

本次不构建、不分发、不安装 APK；真实验签、真机下载及升级留待工具与新 APK 可用后，由最终集成方验收。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q `
  tests/test_app_updates.py tests/test_phone_access.py `
  tests/test_camera_monitoring.py tests/test_alert_center.py
```

测试采用隔离目录、合成 APK 字节/ZIP 和模拟工具输出，覆盖版本/ABI、完整下载、鉴权、路径攻击、损坏/同长度同mtime修改、畸形 manifest、pin/debug拒绝、三 ABI、失败批次、原子切换、旧 digest、工具选择及大小/notes边界。不调用真实模型或付费服务，不把模拟结果当作真实 APK 验签证据。当前文件系统不支持创建真实 symlink 时对应测试明确 skip；另有无需该权限的 Windows reparse 拒绝用例。
