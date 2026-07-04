# Yang-gumi Personal Rating Site

一个本地部署、单用户使用的 ACGN 私人评分与记录网站。数据默认只保存在自己的电脑上；Bangumi 接入只读取公开条目信息，不需要登录，也不会修改 Bangumi 账户。

## 主要功能

- 动画、漫画、轻小说、游戏等条目的新增、编辑、删除、搜索与标签管理
- 0–10 分个人评分、分项评分、排行与 Bangumi 公共评分对比
- Bangumi 搜索、手动绑定、封面、简介、评分与排名缓存
- 当季新番浏览、状态关联与本地海报缓存
- “今日美图”本地图库，支持竖屏与壁纸文件夹、智能焦点裁切和定时重建索引
- JSON、CSV、SQLite 备份、恢复与只读局域网分享
- 所有私人数据、令牌、缓存和本地图片都被 Git 忽略

## Windows：第一次使用

1. 安装 [Python 3.11 或更高版本](https://www.python.org/downloads/)，安装时勾选 **Add Python to PATH**。
2. 下载本仓库：右上角 **Code → Download ZIP**，解压到一个长期保留的文件夹。
3. 双击 **`安装并启动 Yang-gumi.bat`**。脚本会创建独立环境、安装依赖并打开网站。
4. 浏览器默认打开 `http://127.0.0.1:8501`。首次运行会自动创建空数据库。
5. 以后双击 **`启动 Yang-gumi.bat`** 即可。

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
4. 首页点击“竖屏”或“壁纸”，为“今日美图”选择本机图片文件夹，再点击“重新扫描图片”。
5. 在“数据管理”中创建 SQLite 备份；迁移电脑时保留备份文件即可。

## 数据与隐私

运行后产生的数据库、备份、导出文件、令牌、日志、封面与图片缓存均不会进入 Git。不要强制添加 `data/`、`backups/`、`exports/`、`covers/` 或 `static/` 下的运行时文件。

仓库不附带作者的评分数据库、私人笔记、图片、访问令牌或本机路径。详情见 [隐私说明](docs/PRIVACY.md)。

## 文档

- [部署与迁移](docs/DEPLOYMENT.md)
- [使用说明](docs/USER_GUIDE.md)
- [隐私说明](docs/PRIVACY.md)
- [`docs/Yang-gumi_新电脑部署与完整使用说明_20260704.docx`](docs/Yang-gumi_新电脑部署与完整使用说明_20260704.docx)（适合离线阅读）

## 开发与测试

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

项目采用 [MIT License](LICENSE)。第三方条目信息与图片仍受各自来源的条款和著作权约束。
