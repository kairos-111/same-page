# 听课桌面开发工程

Tauri 2 + 当前本地Python/MLX/llama.cpp内核。先开发界面和程序，后续再做独立打包。

## 本机启动

从项目根目录运行 `.venv/bin/python scripts/desktop.py`，或双击 `outputs/启动听课桌面版.command`。这会打开本机构建的开发.app，不会自动录音。

## 本机编译

项目首次开发依赖已准备在被忽略的work目录。完整源码、Rust及npm锁文件在本目录；此说明不是干净电脑安装器。

```sh
npm install --prefix desktop
CARGO_HOME="$PWD/work/devtools/cargo" RUSTUP_HOME="$PWD/work/devtools/rustup" CARGO_TARGET_DIR="$PWD/work/desktop-target" PATH="$PWD/work/devtools/cargo/bin:$PATH" cargo build --manifest-path desktop/Cargo.toml
.venv/bin/python scripts/desktop.py
```

没有本机工具链时，先按Rust官方方式准备工具链，再调整上述路径；不要复制.venv或模型当成完整部署方案。实际状态与未验证范围见项目outputs/PRD.md。

## 运行边界

开发期原生窗口连接127.0.0.1:8767，复用已存在的合法服务，或启动项目内Python服务。应用只结束自己启动的服务。模型、课堂数据仍在原位置。无自动更新、签名公证、模型下载/分发或通用安装包。
