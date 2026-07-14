# Yang-gumi Personal Rating Site

一个本地部署、单用户使用的 ACGN 私人评分与记录网站。数据默认只保存在自己的电脑上；Bangumi 接入只读取公开条目信息，不需要登录，也不会修改 Bangumi 账户。

## 主要功能

- 动画、漫画、轻小说、游戏等条目的新增、编辑、删除、搜索与标签管理
- 0–10 分个人评分、分项评分、排行与 Bangumi 公共评分对比
- Bangumi 搜索、手动绑定、封面、简介、评分与排名缓存
- 当季新番浏览、状态关联与本地海报缓存
- “今日美图”本地图库，支持竖屏与壁纸文件夹、智能焦点裁切和定时重建索引
- JSON、CSV、SQLite 备份、恢复与只读局域网分享
- 数据库、令牌、缓存和本地图片默认不会被 Git 跟踪

## Windows：安装前先准备

1. 使用 **64 位 Windows 10 或 Windows 11**，预留至少 **4 GB** 可用空间。
2. 从 Python 官网安装 **Python 3.13.x 64 位完整版（推荐）**。项目支持 Python 3.10-3.14，但发布测试以 3.13 为基线。安装界面必须勾选 **Add python.exe to PATH**。
3. 安装后打开“命令提示符”，运行 `py -3.13 --version`；能显示版本号再继续。
4. 首次安装依赖和首次读取 Bangumi 数据需要联网。普通 ZIP 用户不需要提前安装 Git、Node.js、Java、数据库或开发工具。
5. 准备 Chrome、Edge 或 Firefox。正常情况下无需浏览器扩展；非常旧的 Edge 若仍把原生控件显示成白色，可按文档使用 Dark Reader 作为单站点兜底。Internet Explorer 不受支持。

Python 官方下载页：https://www.python.org/downloads/windows/

## Windows：第一次使用

1. 确认已经完成上面的 Python 3.13.x 64 位安装与版本检查。
2. 下载本仓库：右上角 **Code → Download ZIP**，解压到一个长期保留的文件夹。
3. 双击 **`安装并启动 Yang-gumi.bat`**。脚本会创建独立环境、安装依赖并打开网站。
4. 浏览器默认打开 `http://127.0.0.1:8501`。首次运行会自动创建空数据库。
5. 以后双击 **`启动 Yang-gumi.bat`** 即可。主站启动器不会连锁开启只读分享；只有单独双击 **`启动只读分享.bat`** 才会运行 8502 服务。

在虚拟机中使用时，建议至少分配 2 个处理器核心和 4 GB 内存，并确认虚拟机可以正常访问互联网。无 VPN 或代理不会影响本地条目管理，但 Bangumi 搜索、季度数据和封面下载可能因网络状况变慢；等待片刻或稍后重试即可。

## 局域网只读分享

1. 在主人电脑的项目目录中双击 **`启动只读分享.bat`**，首次运行时允许 Windows 防火墙放行专用网络的 8502 端口。
2. 保持主人端只读分享进程运行。脚本会把当前访问令牌和局域网地址写回同一个 BAT。
3. 将更新后的 **`启动只读分享.bat`** 单独复制到另一台 Windows 电脑或虚拟机；访客不需要 Python，也不需要复制项目目录。
4. 访客双击该文件后会自动寻找主人电脑，并以直连模式打开 Edge，避免失效系统代理导致无法访问。

VMware NAT 用户如果仍无法连接，应在主人电脑防火墙中仅对 VMnet8 网段放行 TCP 8502。例如主人地址为 `192.168.81.1` 时，可将远程范围限制为 `192.168.81.0/24`。不要把 8502 端口转发到公网。

如果只想使用命令行：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python start_yanggumi.py
```

## macOS / Linux

```bash
chmod +x install_and_start.sh
./install_and_start.sh
```

在没有桌面文件夹选择器的服务器上，可先设置 `YANGGUMI_PORTRAIT_DIR` 与 `YANGGUMI_WALLPAPER_DIR`；示例见 `.env.example`。

## 最初的五分钟

1. 进入“新增条目”，输入中文标题并搜索 Bangumi。
2. 确认匹配结果，填写状态、个人评分、短评和标签后保存。
3. 在“条目库”中查看、筛选或编辑；在“排行榜”“评分对比”中看统计结果。
4. 首页点击“竖屏”或“壁纸”，为“今日美图”选择本机图片文件夹，再点击“重新扫描图片”；多级子文件夹也会被识别。
5. 在“数据管理”中创建或加载 SQLite 备份；迁移电脑时保留备份文件即可。
6. 需要卸载时双击 `卸载 Yang-gumi.bat`，按提示选择是否先把数据保存到安装目录之外。

## 数据与隐私

运行后产生的数据库、备份、导出文件、令牌、日志、封面与图片缓存均不会进入 Git。不要强制添加 `data/`、`backups/`、`exports/`、`covers/` 或 `static/` 下的运行时文件。

仓库不附带作者的评分数据库、私人笔记、图片、访问令牌或本机路径。详情见 [隐私说明](docs/PRIVACY.md)。

## 文档

- [部署与迁移](docs/DEPLOYMENT.md)
- [使用说明](docs/USER_GUIDE.md)
- [隐私说明](docs/PRIVACY.md)
- [`docs/Yang-gumi_新电脑部署与完整使用说明_20260704.docx`](docs/Yang-gumi_新电脑部署与完整使用说明_20260704.docx)（适合离线阅读）
- [`docs/Yang-gumi配置说明.docx`](docs/Yang-gumi配置说明.docx)（完整修订版，不删减原说明）
- [`docs/Yang-gumi网站视频介绍与部署使用文稿.md`](docs/Yang-gumi网站视频介绍与部署使用文稿.md)（18分钟中文视频口播、画面与注意事项）
- [第三方组件声明](THIRD_PARTY_NOTICES.md)
- [更新记录](CHANGELOG.md)

## 开发与测试

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

项目采用 [MIT License](LICENSE)。第三方条目信息与图片仍受各自来源的条款和著作权约束。
