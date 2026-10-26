# P4 Archive Toolkit

一个本地运行的文献与证据版本工具：用稳定 ID 连接项目、人物、角色、事件、来源、片段、主张和文献，保留修订、来源与不确定性。提供命令行、SQLite 存储和浏览器阅读界面。

Python 3.10+ 标准库即可运行，不需要数据库服务、模型 API 或第三方 Python 包。程序不会自动联网取源、调用模型、上传资料、推送 Git 或部署网站。网页服务只监听本机。

## 快速开始

在仓库根目录执行，先使用合成示例：

```sh
python3 -m p4vault --db local/demo/vault.sqlite3 --local local/demo init
python3 -m p4vault --db local/demo/vault.sqlite3 --local local/demo import fixtures/synthetic.json --namespace demo
python3 -m p4vault --db local/demo/vault.sqlite3 --local local/demo search Synthetic
python3 -m p4vault --db local/demo/vault.sqlite3 --local local/demo serve --port 18776
```

打开 `http://127.0.0.1:18776`，按 Control-C 停止服务。工作数据库和导入快照保存在被 Git 忽略的 `local/` 中，不会成为本仓库内容。

也可使用随仓保留的已发表页面投影：

```sh
python3 -m p4vault --db local/public/vault.sqlite3 --local local/public import public/documents.json --namespace approved-public-snapshot
python3 -m p4vault --db local/public/vault.sqlite3 --local local/public serve --port 18777
```

该投影包含 789 条页面记录和 31 个媒体引用的来源登记；页面记录数不是独立作品数。保留原文链接、版本、署名与范围信息，不包含原图、影音、原始访谈或完整研究数据库。`public/README.md`、`public/scope.json` 和 `public/manifest.json` 保留原投影的历史范围与校验记录；其中的原目的地和可见性字段是上游审计记录，不是当前仓库状态，也不授予将新导入资料公开的许可。

## 功能与目录

- `p4vault/`：导入、关键词检索、版本比较、来源追溯、时间轴、关系图、任务状态、许可检查、备份和恢复。
- `migrations/`、`schemas/`：数据库迁移及数据格式。已应用的迁移以哈希校验，不能原地改写。
- `web/`：本地阅读界面，支持检索、关系导航、修订与证据定位。
- `fixtures/`、`tests/`：合成数据与回归测试，不依赖私人研究库。
- `tools/`：离线校验和显式、按哈希恢复的工具；不含备份载荷。
- `public/`：已有公开来源的精确投影及其上游范围记录。

运行 `python3 -m p4vault --help` 查看命令。导入需要显式输入及 namespace；重复载荷保持幂等。源零点时码、素材版本与机器判断分别记录，未知身份和日期不自动补成事实。

`export-public` 是本地字段投影操作，需要目的地、修订和允许字段绑定的许可；它不会发布到网络。备份/恢复也只操作明确给出的本地路径。请勿把真实研究库、原片、密钥或工作日志加入版本控制。

## 验证

```sh
python3 -m unittest discover -v
python3 tools/release_gate.py
node --check web/app.js
```

最后一项只用于 JavaScript 语法检查，日常运行不需要 Node.js。`release-files.json` 是本公开代码包的逐文件清单；修改代码或文档后应审阅并更新清单。

## 来源与权利

本版从 P4 文献版本系统 v0.1.15 的提交 `283af2e80f4c92ef1666088b2cc9bf11ae6ef23c` 按白名单导出，没有复制 Git 历史。范围与排除项见 [UPSTREAM.md](UPSTREAM.md)。

未添加 MIT 或其他新许可证。代码、作品和资料保持原有权利状态；来源、署名与已有范围记录原样保留。公开可读不等于替资料授予新的改编、再分发或商业使用许可。
