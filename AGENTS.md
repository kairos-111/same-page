# Same Page 开发协作规则

## 开始前

- 先读 `outputs/PRD.md`：唯一的产品需求、当前进度、不可破坏的规则（第 4、7 节）和待办。再按任务读 `outputs/使用说明.md` 和实际代码。
- 项目**已暂停开发**（2026-10-26）。用户会另行给出新要求，不要自动延续旧任务。
- 用户最新要求优先。回复用户用中文。
- 先看 `git status`，尊重未提交修改；不 reset、不 clean。

## 工作范围

- 项目文件夹是 `~/Documents/Same Page/`，本仓库为其中 `源码/`。所有工作只在这个文件夹里进行，新文件也只放这里：代码与文档进 `源码/`，交付物进编号文件夹。
- 用户数据在 `~/Library/Application Support/听课/`（应用依赖这个位置，不移动）。

## PRD 按用户要求统一更新

- 日常迭代不改 PRD 和使用说明。用户明确要求时，把这期间的改动合并成**一个**新版本（一条版本记录、一次版本号递增）。为此，提交信息要写清改了什么、验证了什么。
- 区分已实现、已验证、计划；不把代码存在写成已在真实课堂验证，不把没跑的测试写成通过。
- 不另建第二份 PRD。

## 运行与数据保护

- 本机应用 8877/8878；开发版 8767/8768。启动、停止、重启或占用模型前先查对应 `/api/state`；有 loading/running/draining 会话时不干扰。
- 不自动录音，不上传录音，不启用云端 API，不把本地令牌与密钥写进文档或提交。
- 不删除或覆盖 `outputs/sessions/`、`.venv/` 和 `work/` 中的模型与运行时。
- 不做 90 分钟长测；只跑与改动相称的检查。
- 签名、公证等账号密码由用户本人输入。

## 测试、提交与部署

日常只跑与改动相关的关键测试（加 `node --check` 和 `git diff --check`），通过后直接提交，无需征求确认。**推送 GitHub 需要用户同意。**

```sh
.venv/bin/python -m unittest discover -s tests -v
node --check classroom/static/app.js
git diff --check
```

提交后直接装进本机应用：先查 8877 `/api/state`，有会话或模型正在启停就暂缓并告知用户；空闲时运行 `work/deploy.sh`，或手动把 `classroom/` 用 rsync（排除缓存）同步到 `01_本机应用/Same Page.app/Contents/Resources/engine/classroom/`，`scripts/serve.py` 一并同步。改了 `desktop/`（含 `desktop/icons/`：程序坞用的是编进程序的 `icon.png`）才重新编译 release 程序替换 `Contents/MacOS/tingke-desktop`，并把 `icon.icns` 复制为 `Contents/Resources/AppIcon.icns`。然后 `codesign --force --deep --sign -`、`--verify --deep --strict`，停掉空闲的旧引擎和应用再重开，核对版本与接口。版本号变化时同步 `bundle.json`、`work/deployment.json` 和 Info.plist。

不提交模型、环境、密钥、私人录音或大型运行产物。可交付文档放 `outputs/`，中间文件和日志放 `work/`。

## 兼容边界

保留 Bundle ID `app.tingke.local`（开发版 `app.tingke.desktop.dev`）、音频助手 ID、`~/Library/Application Support/听课/`、`tk.*` 偏好键和 CSV 真实文件名。两套记录（本机应用与旧开发版）不合并、不回迁。Spotlight 里只应出现 `01_本机应用/Same Page.app`；不要在可索引目录留 .app 副本，更新前的回滚副本放 `work/backups.noindex/`，验收后移入废纸篓。
