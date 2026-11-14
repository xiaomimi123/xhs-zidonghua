# Live 图(iPhone 实况照片)· Mac 端说明

工作台里的「Live 图」页负责**选图、排队、看进度、下载成品**;真正生成实况照片要在 **MacBook** 上跑,
因为实况照片需要 macOS 写入系统认的配对信息(Windows 导入不了,已试过,见项目记忆)。

```
工作台(Windows/Docker)             MacBook
选图 → 任务排队  ──── 领任务 ────▶  渲染 5 秒推近动画 + 关键帧 JPG
任务页看进度/下载  ◀── 回传 .pvt ──  makelive 打成 .pvt 包
                                    双击导入「照片」→ AirDrop 到 iPhone
```

## 一次性准备(Mac)
1. 把项目里的 `tools/livephoto/` 整个文件夹拷到 Mac(`make_live.py` 和 `mac_worker.py` 必须在一起)。
2. 终端进到这个文件夹:
   ```bash
   pip3 install -r requirements-mac.txt
   ```
   依赖:`pillow`、`imageio-ffmpeg`(自带 ffmpeg,不用另装)、`makelive`。
3. 工作台电脑和 Mac 要在同一个局域网;地址用工作台电脑的局域网 IP(不是 127.0.0.1)。

## 日常使用
```bash
python3 mac_worker.py --server http://工作台IP:18765 --token 采集端Token
```
- Token:管理端「接口配置」里的采集端 Token(和采集端共用同一个)。
- 窗口开着就会自动领任务;工作台「Live 图」页顶部会显示「Mac 已连接」。
- 成品保存在 `~/LivePhotos/IMG_L<任务号>_LIVE.pvt`,同时回传工作台(任务页可预览、下载)。
- 导入 iPhone:双击 `.pvt` 导入 Mac「照片」,再 AirDrop;或直接把 `.pvt` 文件夹 AirDrop 过去。
- 发布仍只能用 iPhone 上的小红书 App:选图时选这张 Live 图,点「实况」。

### 做完自动导入 Mac 相册(按人建文件夹,里面按天分相册)
加 `--import-photos`,每做完一个 `.pvt` 就自动导入本机「照片」App,成品照常回传工作台。
(已验证:macOS 照片会按真品 Live Photo 配对存储,iCloud 照片开着的话还会同步到 iPhone。)

照片 App 侧边栏的结构(工作台 claim 接口下发 `by` 发起人和 `created` 任务日期):
```
张怡            ← 文件夹,一个人一个
 ├ 2026-11-14   ← 相册,一天一个
 └ 2026-11-13
润娜
未署名          ← 工作台没给发起人的任务
```
侧边栏平时只有几个人名,点开才是日期。导入时直接 `import … into album`,不再按文件名去猜刚导入的是哪一张,
所以不会再出现空相册或放错相册。导入没成功会在终端里写明原因(不影响回传)。
```bash
python3 mac_worker.py --server http://工作台IP:18765 --token 采集端Token --import-photos
```

**第一次用(或更新代码后)先做两步:**
```bash
cd tools/livephoto
python3 organize_photos.py --selftest   # 建个临时文件夹和相册再删掉,确认「照片」的脚本命令能用
python3 organize_photos.py              # 只看计划:旧的「张怡 · 2026-11-14」平铺相册会搬到哪里、哪些是空的
python3 organize_photos.py --apply      # 真正整理:照片加进新相册,旧相册删掉(只删相册,照片本身不动)
```

**分发给同事**:侧边栏点开某个人的文件夹 → 点日期相册 → ⌘A 全选 → 右键 / 分享 → AirDrop 到她的 iPhone;
或者她的 iPhone 和这台 Mac 用同一个 iCloud,照片会自己同步过去。
(照片 App 里文件夹内的相册顺序是手动的,新相册加在最后,所以日期从旧到新排。)

### 导入后自动加进每个人的 iCloud 共享相簿(`--share`)
「照片」的脚本接口不能操作共享相簿,所以绕一步:导入成功后,先把这张图放进「待分享」文件夹里这个人的**暂存相册**
(里面只有我们放进去的图,iPhone 同步来的照片混不进来),再运行这个人的快捷指令「加入共享-张怡」,由快捷指令加进她的共享相簿。
```bash
python3 mac_worker.py --server http://工作台IP:18765 --token 采集端Token --share     # --share 已包含 --import-photos
```
**一次性设置**(每个人一次):
1. 在「照片」里建好每个人的共享相簿(侧边栏「共享相簿」旁 + ,「邀请他人」框里输 Apple ID 邮箱 / 手机号回车),同事在 iPhone 上接受邀请。
2. 建暂存相册并看每人的快捷指令配方:
   ```bash
   python3 share_setup.py --from-server http://工作台IP:18765 --token Token    # 从工作台读出所有发起人;也可以 --people 张怡 润娜
   ```
3. 在「快捷指令」App 里为每个人建一个,名称必须是 `加入共享-名字`:
   ① 查找照片:相册 是「待分享 / 张怡」,排序「添加日期 · 最新在前」,限制 1 张 ② 添加到共享相簿:选「张怡」的共享相簿。建好一个后「复制」再改这两处最快。
4. `python3 share_setup.py --check` 看每个人的快捷指令 / 暂存相册是否齐了;`--test 张怡` 真的跑一次(会往她的共享相簿里加一张)。
5. 如果快捷指令里看不到「待分享」文件夹里的相册,改用顶层暂存相册:`share_setup.py --flat ...` 建,worker 加 `--share-flat`。

没建快捷指令的人、没有发起人(未署名)的任务会**自动跳过共享**,终端里写明原因,导入和回传不受影响。
共享失败(快捷指令出错)会自动重试一次;还不行,图已经在「待分享」暂存相册和日期相册里,可以手动加进共享相簿。
同事要发小红书时:在共享相簿里点这张图 → 存到相册 → 小红书里选它点「实况」。

**补导漏导入的包**:`python3 reimport_missing.py --scan ~/LivePhotos --dry-run` 先看清单,去掉 `--dry-run` 再执行;
会按路径 `~/LivePhotos/发起人/日期/` 放进对应的文件夹和相册。

**改代码后跑单元测试**(不需要照片 App):`python3 -m unittest test_photos_lib -v`

## 不连工作台,只处理一个文件夹
```bash
python3 mac_worker.py --local ~/Pictures/待做 --out ~/LivePhotos
```
可选参数:`--zoom 1.08`(推近幅度)、`--focus-y 0.35`(焦点,0 顶 ~ 1 底)、`--seconds 5`、`--fit crop|blur`。

## 参数含义
| 参数 | 说明 |
|---|---|
| 推近 | 5 秒里从 1 倍缓慢放大到 1.05 / 1.08 / 1.15 倍 |
| 焦点 | 放大(和裁切)时画面的视觉中心,越小越偏上 |
| 非 3:4 的图 | 成品库的图常是 9:16。`crop` 铺满后裁成 3:4;`blur` 完整显示,上下/左右用放大虚化的原图补边 |

## 排查
- **连不上工作台**:地址别用 127.0.0.1;检查防火墙、端口、Token。
- **导入后只是一张静态图**:先确认 `python3 make_live.py --check 图.JPG 视频.MOV` 里两边内容 ID 一致、有静态帧轨;
  再看 iPhone 上是不是用「照片」App 导入的(微信 / 网页版 iCloud 会丢配对信息)。
- **任务一直是「生成中」**:领走超过 20 分钟没回传会自动退回排队,Mac 重新启动后会再领到。


## 两种效果
| 效果 | 谁来做 | 说明 |
|---|---|---|
| 慢推镜头 | 只需要 Mac | 把图做成缓慢推近的动画,快,不用显卡;但是整张图一起放大,画面是「平」的 |
| 真实动作 | 先由装了 ComfyUI 的电脑生成视频,再由 Mac 做成实况 | 图生视频(MiniMax H3),手持轻晃 / 手转产品 / 镜头靠近,更像手机随手拍的真实实况 |

```
选图 → [视频 worker:本机显卡,约 40 秒/条] → 视频 → [Mac:makelive 打包 .pvt] → AirDrop / iCloud → iPhone 相册 → 小红书
```
任务状态:排队 · 等待生成视频 → 生成视频中 → 视频已好 · 等待 Mac → Mac 制作实况中 → 已完成。视频好了可以先在任务页点「看视频」。

## 视频 worker(只有「真实动作」需要)
在装了 ComfyUI + MiniMax H3(UNET / 4 步加速 LoRA / Qwen3-VL 文本编码器 / 视频与音频 VAE)和 MiniMax H3 Director 节点的电脑上:
```bash
python tools/livephoto/video_worker.py --server http://工作台地址:18765 --token 采集端Token
```
- 默认调用本机 `http://127.0.0.1:8188` 的 ComfyUI(`--comfy` 可改),输出 864×1152、3 秒、24fps(约 70 秒/条)。分辨率会直接影响包装上小字是否清晰:576×768 字会糊成一团,768×1024 基本能认,864×1152 和 960×1280 与原图几乎一致;`--size` 可改。
- ComfyUI 不需要对外开放:worker 主动连工作台领任务、把视频传回去。
- 动作提示词统一写成「手机随手拍的家庭视频、手抖、自动曝光、不要电影感」,模板在 `app/api_live.py` 的 `MOTIONS`。
- 需要 Pillow;ComfyUI 里没有 SageAttention 补丁节点也能跑(只是可能更慢)。

Mac 端处理真实动作任务时:关键照片默认用原图(和视频用同一个裁切,包装上的字清晰);加 `--first-frame-still` 改取视频第一帧(和视频画面完全一致,但视频分辨率较低);视频里模型生成的声音默认去掉,加 `--keep-audio` 保留。

## 已知问题
- 真实动作的视频里,产品包装上的小字转动时可能走样;「手转产品」最明显,「手持轻晃」最稳。
- 生图提示词已改成「手机随手拍的生活照」风格(`app/covers.py` 的 `UGC_STYLE` 和场景库),不再是棚拍商业风。

## 相关文件
- `app/api_live.py`:任务队列与接口(用户端 `/api/live/*`,Mac 端 `/api/live/worker/*`)
- `static/live.js`:「Live 图」页
- `tools/livephoto/make_live.py`:渲染动画、写 JPG/MOV 元数据(也可单独命令行用)
- `tools/livephoto/mac_worker.py`:Mac 端领任务脚本
- `tools/livephoto/video_worker.py`:视频 worker(本机显卡 + ComfyUI,真实动作模式)
- `tools/livephoto/imgfit.py`:任意比例的图适配成 3:4(只依赖 Pillow)
- `evals/cases/iso_live.py`:任务队列的隔离测试(在容器里跑回归时会一起跑)
