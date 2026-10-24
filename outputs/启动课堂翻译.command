#!/bin/zsh
cd "$(dirname "$0")/.." || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "运行环境尚未准备完成，请先完成部署。"
  read -r "?按回车关闭"
  exit 1
fi
.venv/bin/python scripts/launch.py
if [ "$?" -ne 0 ]; then
  read -r "?启动失败，按回车关闭"
  exit 1
fi
