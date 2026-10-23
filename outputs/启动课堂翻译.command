#!/bin/zsh
cd "$(dirname "$0")/.." || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "运行环境尚未准备完成，请先完成部署。"
  read -r "?按回车关闭"
  exit 1
fi
echo "课堂翻译地址：http://127.0.0.1:8767"
echo "首次开始时会加载模型；关闭此窗口会停止服务。"
.venv/bin/python scripts/serve.py
