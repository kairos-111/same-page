# Same Page｜本地 AI 字幕与翻译

**每一句，都跟得上。**

在 Apple Silicon Mac 上运行的桌面应用：把英语、俄语或英俄中混合的声音变成原文字幕，并用本地模型直接译成简体中文。识别与翻译都在本机完成，不上传。

**当前状态：开发暂停（2026-10-26）。** Mac 版 0.10.2 可日常使用；Windows 安装包已构建但未在真机运行过。

## 从哪里看

- [outputs/PRD.md](outputs/PRD.md)：唯一的产品需求、当前进度、规则与待办。
- [outputs/使用说明.md](outputs/使用说明.md)：怎么用。
- [outputs/安装包规划.md](outputs/安装包规划.md)、[outputs/Windows安装说明.md](outputs/Windows安装说明.md)：安装包。
- [AGENTS.md](AGENTS.md)：开发协作规则。

## 项目文件夹

本仓库是 `~/Documents/Same Page/源码/`。同级有本机应用、Windows 安装包、文档链接、历史记录、课堂记录入口和开发运行材料，见上一级的 `00_先看这里.md`。

## 源码结构

| 目录 | 职责 |
|---|---|
| classroom/ | Python 业务：本地 API、识别、字幕、翻译调度、保存与记录管理；static/ 为界面；wlk/ 为内置的流式识别算法 |
| desktop/ | Tauri/Rust 桌面窗口 |
| native/ | Swift 麦克风与电脑声音采集 |
| windows/ | Windows 启动器、构建脚本与 NSIS 安装脚本 |
| scripts/ | 启动、装配、恢复、去重脚本 |
| tests/ | 回归测试 |
| outputs/ | PRD、使用说明、术语表、旧开发版记录；archive/ 为早期资料 |
| work/ | 模型、运行时、工具链、构建产物（不进 Git，见 work/README.md） |

## 运行

- 日常使用：双击项目文件夹里的 `Same Page.app`（端口 8877/8878，数据在 `~/Library/Application Support/听课/`）。打开应用会自动加载模型，不会自动录音。
- 开发版：`outputs/启动课堂翻译.command`（浏览器 8767）或 `outputs/启动听课桌面版.command`（开发窗口，需先编译）。开发版使用仓库内的独立数据，两版记录不合并，不要同时跑模型。

仓库不含模型、环境和私人录音。GitHub：[kairos-111/offline-classroom-translator](https://github.com/kairos-111/offline-classroom-translator)（私有）；当前分支 `fix/recording-playback-import` 未推送。
