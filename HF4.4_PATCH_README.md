# HearPitch HF4.4 ROSVOT 导入路径修复补丁

## 修复内容

修复 ROSVOT 推理时上游模块导入失败：`ModuleNotFoundError: No module named 'utils'`。HearPitch 现在会把本机 ROSVOT 源码目录加入独立推理子进程的 `PYTHONPATH`，使顶层 `utils` 包能被找到。HF4.4 也包含 HF4.3 对真实推理退出码和日志的诊断修复。

## 覆盖升级步骤

1. 关闭 HearPitch 网页服务及其命令窗口。
2. 将补丁 ZIP 解压到 HearPitch 项目文件夹的**上一级目录**，允许合并 `HearPitch` 文件夹并覆盖同名文件；或者只手动覆盖：
   `HearPitch/hearpitch_core/rosvot_engine.py`
3. 重新运行 `start_windows.bat`，再次尝试转谱。

此补丁不要求重新安装 ROSVOT，不会触碰 `.venv`、`Documents\\HearPitchLocal` 下的模型/权重、用户配置或项目数据，也无需重下约 557 MiB 的模型。

若任务再次失败，请把新的完整错误提示和 HearPitch 命令窗口中对应的 ROSVOT 日志发回，以便按真实子进程错误继续排查。

版本：V260924A HF4.4
GitHub commit：见 GitHub `main` 最新提交
