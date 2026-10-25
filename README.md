# Same Page｜本地 AI 字幕与翻译

**每一句，都跟得上。**

Apple Silicon Mac上的桌面应用，Whisper MLX提供英语、俄语、英俄中混合三种识别模式，Hy-MT2直接译中文；中文片段统一简体后保留，混合模式试用。

交接入口：[Claude Code交接说明](outputs/ClaudeCode交接说明-20261025.md)。后续要求由用户提供；当前工作区另有未验收新增改动，详见交接。

## 从哪里看

- 产品需求、当前实现、验证边界与遗留问题：[outputs/PRD.md](outputs/PRD.md)，唯一基准。
- 操作说明：[outputs/使用说明.md](outputs/使用说明.md)。
- 早期测量：[outputs/短测验收报告.md](outputs/短测验收报告.md)，按样本与日期理解。
- 开发协作规则：[AGENTS.md](AGENTS.md)。

用户集中查看入口：`/Users/z/Documents/Codex/2026-10-24/users-z-documents-codex-20261024-md/outputs/Same Page项目/`。其中“02_开发源码”指向本仓库，没有复制第二套源码。

## 源码结构

| 目录 | 职责 |
|---|---|
| classroom/ | Python业务程序：本地API、识别、字幕、翻译调度、历史保存与网页界面 |
| classroom/static/ | 桌面窗口内的HTML/CSS/JavaScript界面 |
| desktop/ | Tauri/Rust窗口、后台进程管理、桌面配置与依赖锁 |
| native/ | Swift麦克风及电脑声音采集组件源码 |
| scripts/ | 正式启动、构建、恢复和检查脚本；bundle_local.py仅在明确需要重建时运行 |
| tests/ | 受控回归测试和前端状态脚本 |
| outputs/ | 唯一PRD、使用说明、术语、旧开发版课堂/采集组件；archive/为早期参考 |
| work/ | 模型、离线材料、运行时、工具链、构建产物及归档验证材料，见work/README.md |
| .venv/ | 当前已安装的开发Python环境，不作为分发环境复制 |

## 运行与数据

独立应用已更新为0.10.1 Same Page，位于集中入口的“01_本机应用”。双击“Same Page.app”或“打开 Same Page.command”即可；不会自动录音。独立版使用8877/8878，数据在`~/Library/Application Support/听课/`。

开发模式仍可使用`outputs/启动课堂翻译.command`（浏览器8767）或`outputs/启动听课桌面版.command`（开发窗口）；使用项目原有数据。两版不会自动合并记录，不要同时运行模型。

安装包制作暂停，继续完善功能。必要管理功能短测已通过，不代表识别延迟/完整性或全部设备权限已验收。每次产品或部署变更同步PRD，不自动开始麦克风或90分钟测试。

仓库不含模型、环境和私人录音。GitHub：[kairos-111/offline-classroom-translator](https://github.com/kairos-111/offline-classroom-translator)，私有。当前本轮开发改动尚未提交/推送。

0.9.0更新品牌与通用记录文案；0.9.1将一键启动改为加载Small、Turbo和Hy-MT三个模型，两个识别模型独立驻留，支持单项停止。0.10.0新增三种面向中文的模式，混合为试用；当前不提供反向互译。旧数据目录、Bundle ID和阅读偏好保持兼容，旧应用入口留链接。

0.10.1修复混合模式语言误拒及短片段清缓冲；有界等待上下文、缺口信息收进详情，37秒用户授权原声对照详见PRD D.6。

源码0.10.2（尚未装入本机应用）：录音结束后只存一个WAV；应用内回听可完整播放和拖动，换记录不再播旧录音；导入文件不按原速处理并显示进度。详见PRD附录D.8。
