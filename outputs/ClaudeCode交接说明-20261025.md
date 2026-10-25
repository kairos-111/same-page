# Claude Code 接手说明（2026-10-25）

## 入口与当前任务

实际开发仓库：`/Users/z/Documents/Codex/2026-10-23/1-whisper-2-hy-mt2-1`。
用户集中查看目录：`/Users/z/Documents/Codex/2026-10-24/users-z-documents-codex-20261024-md/outputs/Same Page项目`；其中 `02_开发源码` 是仓库链接，不是第二份源码。

先读仓库 `AGENTS.md`、`outputs/PRD.md`（唯一PRD）和 `outputs/使用说明.md`。后续需求由用户另行提供；本次只是交接，不继续加功能、做安装包或提交推送。

## 当前产品

Same Page，主口号“每一句，都跟得上。”。软件0.10.1，本机Tauri应用，内含Python、Whisper Small/Turbo、Hy-MT翻译模型和原生声音采集组件。

- 支持英语→中文、俄语→中文、英俄中混合→中文（试用）；Whisper使用 `task=transcribe`，保留原语言。中文直接保留，不先翻成英文；中文原文/译文统一简体，使用本地OpenCC。
- 支持麦克风、电脑声音、导入文件；一次一个活动会话。
- 模型顺序Small、Turbo、Hy-MT；一键启动三个。右侧运行显示绿色“启动”，停止显示红色“已停止”，点击切换；启动模型不等于开始录音。
- 结束必须选择保留/不保留录音，没有“稍后”。不保留只删音频，字幕与记录保留。历史支持直接阅读、导出、删除录音和删除整条记录。
- 最新用户偏好：字幕正文不要黄色“原文未确认/待核查”卡片、系统提示行或空段。缺口信息进“详情”，拒绝时撤回旧预览；后台质量检查、审计和导出仍保留，实际阻断错误仍提示。

## 代码与部署

`classroom/` 是Python业务；`classroom/static/` 是界面；`desktop/` 是Tauri/Rust；`native/` 是Swift采集；`scripts/` 是启动/构建；`tests/` 是回归。
重点文件：`asr.py`、`language_detection.py`、`asr_quality.py`、`asr_recovery.py` 负责识别；`session.py` 串联流程；`translation.py` 调度翻译；`store.py`/`history.py` 保存与管理；`app.py` 提供API。

本机应用：集中目录 `01_本机应用/Same Page.app`，引擎位于其 `Contents/Resources/engine`。**修改仓库不会自动更新此应用**；部署前核对源码与包内资源，只在空闲时更新并重新签名/重开。不要盲跑旧部署脚本，部分脚本仍含旧“听课项目”路径。

独立应用端口8877/8878，用户数据在 `~/Library/Application Support/听课/outputs/sessions/`；旧开发版端口8767/8768，数据在仓库 `outputs/sessions/`。两套历史不要合并，避免恢复用户已经删除的记录。保留现有Bundle ID、旧数据目录和偏好键兼容。安装包、公证和公开分发继续暂停。

## 必须保留的识别约定

ASR兼容原三项返回，也可能第四项带 `{events, rejected, waiting}`。`waiting=True` 时不能清缓冲、强制flush、定稿或送翻译；混合模式最多等2秒新音频。结束、高积压和超窗有保护。拒绝结果保留时间区间和审计，不能靠删除重复词伪装成功。
混合语言探测是提示，不把绝对概率当识别准确度；稳定语言先验取最后明确声学探测。不要退回无限定语言解码，也不要让旧翻译结果覆盖当前中文直通版本。

## 验证边界与工作区

分支main，已提交基线 `0eaecda`；之后大量功能修改仍未提交、未推送。先看 `git status`，禁止reset/clean或覆盖他人修改。

此前识别修复有99项Python受控检查；最新正文提示调整有7项前端专项通过。用户授权37秒原声对照中，旧4个未确认区间改后0个，但没有人工逐词参考稿，不能称零错词或课堂延迟达标。报告在集中目录 `04_验证记录`，私有原声及日志在仓库 `work/validation/`，不要提交或上传。

交接时另发现未跟踪的 `classroom/library.py` 及记录库、显示设置、段落复制、记录字段/搜索、偏好相关新测试；相关API/界面也有接入。**这些新增改动未由本交接轮验收，不能套用上述99项/7项结论，也不保证已部署。** 接手后按用户新需求先核对实际差异。混合连说、短外语词、噪声、轻声及长时间表现仍待验证；PRD的B01/B02/B13等真实质量待办继续开放，其他遗留见PRD。

## 操作约束

- 启停、重启或占用模型前先查对应 `/api/state`；有loading/running/draining会话时不要干扰。不要自动录音，不打印令牌，不上传原声。
- 保留 `work/`、`.venv/`、历史和已下载模型；它们不是可随意删除的缓存。
- 每次变更同步唯一PRD；涉及行为也同步使用说明。区分已实现、已测试、待测试。
- 按改动做必要检查，不自动做90分钟长测。常用：`.venv/bin/python -m unittest discover -s tests -v`、`node --check classroom/static/app.js`、相关 `tests/*.cjs`、`git diff --check`。仅此次文档交接无需启动模型。
