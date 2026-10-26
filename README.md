<p align="center"><img src="desktop/icons/icon.png" width="112" alt="Same Page 图标"></p>

<h1 align="center">Same Page</h1>

<p align="center"><b>每一句，都跟得上。</b><br>本地 AI 驱动的英俄中三语实时字幕与翻译工具</p>

<p align="center"><i>Offline live captions and translation — English, Russian or mixed speech to Simplified Chinese, running entirely on your own computer.</i></p>

---

Same Page 把你正在听的英语、俄语或英俄中混合的声音，实时转成原文字幕，并直接译成简体中文。声音可以来自麦克风、电脑正在播放的声音（网课、视频），或一段已有的录音。识别和翻译都在本机完成，不上传录音，断网也能用。

> **项目状态：开发暂停（2026-10-26）。** Mac 版 0.10.2 可日常使用；Windows 安装包已构建，但还没在真实 Windows 电脑上运行过。完整进度见 [PRD](outputs/PRD.md)。

## 功能

- **三种模式**：英语→中文、俄语→中文、英俄中混合→中文（试用）。中文片段统一为简体后直接保留，不经英文中转。
- **三种声音来源**：麦克风、电脑声音、导入音视频文件（导入不按原速，尽快处理）。
- **实时字幕**：原文与译文左右对照，逐字出现，可分别调字号、只看译文；往上翻看时不打断。
- **结束保存**：自动生成 TXT / SRT / Markdown；录音保留与否由你决定，只存一份。
- **我的记录**：直接阅读全部原文和译文、应用内回放、搜索、收藏、备注、废纸篓。
- **运行数据**：每条记录的出字延迟、识别落后和异常事件，方便判断“慢在哪里”。
- **完全本地**：三个模型随应用打包，打开应用自动加载，退出自动关闭；不需要账号或网络。

## 技术组成

| 部分 | Mac | Windows |
|---|---|---|
| 桌面外壳 | Tauri / Rust | 系统自带 Edge 的应用窗口 + Python 启动器 |
| 本地服务 | Python 3.12 · FastAPI · WebSocket | 同左 |
| 语音识别 | Whisper Small / large-v3-turbo（MLX）+ WhisperLiveKit 流式确认 | faster-whisper（CTranslate2，CPU） |
| 翻译 | [Hy-MT2 1.8B](https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF) GGUF · llama.cpp（Apple GPU） | 同模型 · llama.cpp（Vulkan，失败自动改 CPU） |
| 采集 | Swift（AVAudioEngine / ScreenCaptureKit） | WASAPI（电脑声音用 loopback） |

## 系统要求

- **Mac**：Apple Silicon（M1 及以上），建议 16 GB 内存，约 5 GB 磁盘空间。
- **Windows**：Windows 10/11 64 位，支持 AVX2 的处理器，8 GB 内存起，约 2.5 GB 磁盘空间。安装说明见 [outputs/Windows安装说明.md](outputs/Windows安装说明.md)。

仓库**不包含**模型、运行环境和安装包（体积大，且安装包尚未公开发布）。从源码运行需要本机已准备好的模型与运行时，详见 PRD 第 12 节。

## 文档

| 文档 | 内容 |
|---|---|
| [outputs/PRD.md](outputs/PRD.md) | 唯一的产品需求文档：当前功能、规则、参数、待办、后续方案 |
| [outputs/使用说明.md](outputs/使用说明.md) | 怎么用 |
| [outputs/安装包规划.md](outputs/安装包规划.md) | Mac / Windows 安装包规划 |
| [windows/README.md](windows/README.md) | Windows 版构建说明 |
| [AGENTS.md](AGENTS.md) | 开发协作规则 |

## 源码结构

| 目录 | 职责 |
|---|---|
| `classroom/` | Python 业务：本地 API、识别、字幕、翻译调度、保存与记录管理；`static/` 为界面；`wlk/` 为内置的流式识别算法 |
| `desktop/` | Tauri / Rust 桌面窗口与图标 |
| `native/` | Swift 麦克风与电脑声音采集 |
| `windows/` | Windows 启动器、构建脚本、NSIS 安装脚本 |
| `scripts/` | 启动、装配、恢复、去重脚本 |
| `tests/` | 回归测试（167 项 Python + 前端脚本） |
| `outputs/` | PRD、使用说明、术语表；`archive/` 为早期资料 |

## 开发

```sh
.venv/bin/python -m unittest discover -s tests -v
node --check classroom/static/app.js
.venv/bin/python scripts/launch.py        # 浏览器开发版 http://127.0.0.1:8767/ ，不会自动录音
```

本机的项目文件夹为 `~/Documents/Same Page/`，本仓库位于其中的 `源码/`；`work/`、`.venv/` 和记录目录不进 Git。

## 隐私

录音、字幕和译文只保存在本机（Mac：`~/Library/Application Support/听课/`；Windows：`%LOCALAPPDATA%\Same Page`）。应用只监听本机地址，不上传任何内容，不需要账号。

## 第三方组件

Whisper（MIT）、WhisperLiveKit（Apache-2.0）、Silero VAD（MIT）、faster-whisper / CTranslate2（MIT）、llama.cpp（MIT）、Hy-MT2（Apache-2.0）、OpenCC（Apache-2.0）、Tauri（MIT / Apache-2.0）。Windows 安装包内附完整的 `THIRD-PARTY-NOTICES.txt`。
