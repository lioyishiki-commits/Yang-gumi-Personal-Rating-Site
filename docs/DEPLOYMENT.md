# 部署与迁移

## 运行条件

- Python 3.11 或更高版本
- 支持现代 JavaScript 的浏览器
- 首次安装依赖、搜索 Bangumi、刷新季度数据时需要联网
- 日常条目管理与已缓存内容可以只在本机使用

Yang-gumi 默认只监听 `127.0.0.1:8501`，局域网和互联网中的其他设备无法直接访问主站。

## 新电脑安装

Windows 用户下载并解压仓库后双击 `安装并启动 Yang-gumi.bat`。脚本会在项目目录创建 `.venv`，因此不会改动其他 Python 项目。macOS 或 Linux 使用 `./install_and_start.sh`。

首次运行会自动创建：

- `data/acgn.db`：个人条目和标签
- `data/*.json`：评分、界面、图库和网络缓存设置
- `backups/`、`exports/`：备份与导出
- `covers/`、`backgrounds/`、`static/`：封面、背景与运行时图片缓存

这些内容都在 `.gitignore` 中，不会被正常提交到 GitHub。

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
