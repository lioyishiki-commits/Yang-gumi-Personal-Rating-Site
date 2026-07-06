# 部署与迁移

## 下载项与安装前检查

- 64 位 Windows 10/11（macOS、Linux 也可按下文手动安装）
- 推荐 Python 3.13.x 64 位完整版；支持范围为 Python 3.10-3.14
- Python 安装时勾选 `Add python.exe to PATH`，完成后运行 `py -3.13 --version`
- 至少 4 GB 可用磁盘空间；首次安装依赖时保持联网
- Chrome、Edge 或 Firefox；不需要浏览器扩展，不支持 Internet Explorer
- 首次安装依赖、搜索 Bangumi、刷新季度数据时需要联网
- 日常条目管理与已缓存内容可以只在本机使用

从 ZIP 使用不需要 GitHub 账号，也不需要预装 Git、Node.js、Java、SQLite
管理器或其他数据库。Python 官方 Windows 下载页：
https://www.python.org/downloads/windows/

Yang-gumi 默认只监听 `127.0.0.1:8501`，局域网和互联网中的其他设备无法直接访问主站。

## 新电脑安装

Windows 用户下载并解压仓库后双击 `安装并启动 Yang-gumi.bat`。脚本优先使用 Python 3.13，检查 64 位与版本范围，随后在项目目录创建 `.venv`、安装固定版本依赖并安装旧 Edge 兼容前端，因此不会改动其他 Python 项目。macOS 或 Linux 使用 `./install_and_start.sh`。

首次运行会自动创建：

- `data/acgn.db`：个人条目和标签
- `data/*.json`：评分、界面、图库和网络缓存设置
- `backups/`、`exports/`：备份与导出
- `covers/`、`backgrounds/`、`static/`：封面、背景与运行时图片缓存

这些内容都在 `.gitignore` 中，不会被正常提交到 GitHub。

## 虚拟机使用

在 VMware、Hyper-V 或 VirtualBox 中运行时，建议为 Windows 虚拟机分配至少 2 个处理器核心、4 GB 内存和 4 GB 可用磁盘空间，并使用 NAT 或桥接网络保证可以访问互联网。VMware Tools、Guest Additions 一类增强工具有助于调整分辨率和复制文件，但不是 Yang-gumi 的运行依赖。

网站不需要 Chrome、Edge 或 Firefox 扩展。GitHub 登录也不是下载 ZIP 的必要条件；登录只影响 GitHub 自身的收藏、关注等功能。

没有 VPN、代理或 VPS 时，本地页面、评分和数据库功能仍可正常使用。Bangumi 搜索、季度数据、封面下载以及首次安装 Python 包依赖外部网络，可能出现加载缓慢或偶发超时。先等待请求完成，失败后再重试；不要因为网络慢而重复启动多个安装窗口。

下载 ZIP 后必须先完整解压，再双击启动脚本。不要直接在压缩包预览窗口中运行 BAT 文件。

### 首次安装较慢时

安装窗口长时间没有新输出时，可先确认虚拟机仍能打开普通网页。若 PyPI 下载反复失败，可在项目文件夹打开命令提示符后执行：

```bat
.venv\Scripts\python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

该镜像仅用于下载 Python 包，不是网站运行必须项。安装成功后仍使用 `启动 Yang-gumi.bat` 正常启动。

## 迁移到另一台电脑

1. 在旧电脑“数据管理”页面创建 SQLite 备份。
2. 在新电脑安装并启动一次 Yang-gumi。
3. 在新电脑“数据管理”页面上传并恢复该备份。
4. 重新选择“今日美图”的竖屏和壁纸文件夹；这些路径不会随数据库迁移。
5. 如有自定义背景，重新上传对应图片。

恢复会覆盖当前数据库，操作前先为新电脑现有数据创建备份。

## 手动启动与端口

```bash
python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

固定启动器会检查 `8501` 端口和 Streamlit 健康状态。若端口被其他程序占用，请关闭占用者后重试。

## 今日美图目录

桌面环境可在首页点击“竖屏”或“壁纸”打开系统文件夹选择器。无图形界面的主机可在启动前设置：

```bash
export YANGGUMI_PORTRAIT_DIR="$HOME/Pictures/Yang-gumi/Portrait"
export YANGGUMI_WALLPAPER_DIR="$HOME/Pictures/Yang-gumi/Wallpaper"
```

Windows PowerShell 使用 `$env:YANGGUMI_PORTRAIT_DIR = 'D:\Pictures\Portrait'` 的形式。

## 更新代码

更新前先创建 SQLite 备份。Git 用户可执行 `git pull`，ZIP 用户重新下载后把备份通过“数据管理”恢复到新目录。不要用仓库文件覆盖正在使用的 `data/acgn.db`。
