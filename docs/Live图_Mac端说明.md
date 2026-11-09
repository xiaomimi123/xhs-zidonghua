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

## 相关文件
- `app/api_live.py`:任务队列与接口(用户端 `/api/live/*`,Mac 端 `/api/live/worker/*`)
- `static/live.js`:「Live 图」页
- `tools/livephoto/make_live.py`:渲染动画、写 JPG/MOV 元数据(也可单独命令行用)
- `tools/livephoto/mac_worker.py`:Mac 端领任务脚本
- `evals/cases/iso_live.py`:任务队列的隔离测试(在容器里跑回归时会一起跑)
