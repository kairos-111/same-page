# 本地课堂英语→中文翻译

Apple Silicon Mac 上的个人试用 MVP，Whisper MLX 识别 + Hy-MT2 本地翻译。

GitHub 私有仓库：[kairos-111/offline-classroom-translator](https://github.com/kairos-111/offline-classroom-translator)。首次发布为代码与文档快照，本地原提交历史保留；后续 Git 同步约束见 PRD。

- [产品需求、实现现状、待办与 AI 交接](outputs/PRD.md)
- [使用说明](outputs/使用说明.md)
- [短测验收报告](outputs/短测验收报告.md)
- [开发协作规则](AGENTS.md)

本机使用：双击 `outputs/启动课堂翻译.command`，打开 `http://127.0.0.1:8767/`。

**每次产品代码、配置或部署变更，都必须在同次变更中同步更新 PRD。** 接手前先读 `AGENTS.md` 和 PRD，检查用户是否正在录音。

本仓库不包含模型、Python 环境和私人录音；当前部署还依赖本机 `work/` 中的安装材料。迁移前先查看 PRD 的部署缺口，不能假定克隆后直接可用。
