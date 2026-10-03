# HearPitch Local — V261003A

**HearPitch Local** 是一个仅在本机运行的单旋律自动听谱 MVP。它将清唱、口哨或单旋律乐器独奏转换为**可编辑候选谱**，并导出 MIDI、MusicXML 和 JSON。它不是完整混音歌曲、和弦、多声部或自动歌词的可靠转录器。

> 重要：转谱结果是候选谱。请通过原音回听和人工编辑确认音高、时值、调性与拍号。

## 1. 功能边界

| 功能 | V261003A 状态 |
|---|---|
| 本地 MP3/WAV/FLAC/OGG/M4A/AAC 输入 | 支持；依赖 FFmpeg 解码非 WAV 格式 |
| 命令行批量转谱 | 支持；默认不限制时长 |
| 本地网页上传、预览、编辑和导出 | 支持；默认建议 300 秒以内，可启用长音频模式 |
| MIDI、MusicXML、JSON 导出 | 支持 |
| 单旋律音高与音符候选 | 支持；优先使用可选 Basic Pitch，未安装时回退到 pYIN |
| 规则校验 | 支持；提示低置信度、极短音符与可疑跳进 |
| OpenAI 兼容接口的可选 AI 谱面校对 | 支持；只发送结构化音符事件，**不发送原始音频** |
| Windows Credential Manager 保存 API Key | 支持；密钥不会写入 `profiles.json` |
| ROSVOT + RMVPE 歌声转谱 | HF4 可选；需要独立 Python 3.9 + NVIDIA CUDA 环境和单独下载模型 |
| 本机声谱图校正工作台 | V261003A：显示音符候选、播放光标，拖动修改音高/时间；空格打拍估算 BPM |
| 完整歌曲扒谱、分轨、多声部、自动中文歌词 | 不支持，后续版本再评估 |

## 2. Windows 快速启动

### 前置条件

需要安装以下软件并确保能在 PowerShell 或命令提示符中使用：

1. **Python 3.11 (64-bit)**。建议从 [python.org](https://www.python.org/downloads/) 安装，并勾选 “Add Python to PATH”。
2. **FFmpeg**。用于读取 MP3、M4A、FLAC 等格式。将 `ffmpeg.exe` 所在目录加入系统 PATH；如果只处理 WAV，也可以先不装，但不建议。

将整个项目文件夹复制到 Windows 电脑后，双击 `start_windows.bat`。首次启动会创建 `.venv` 虚拟环境并安装基础依赖；完成后浏览器打开：

```text
http://127.0.0.1:8765
```

> **V261003A：** 在已有 ROSVOT + RMVPE CUDA 歌声转谱引擎基础上，新增本机声谱图校正工作台。命令行入口仍为 `hearpitch_cli.py`，核心包为 `hearpitch_core`。旧目录若遗留根目录 `hearpitch.py`，启动脚本会安全忽略；不要执行旧的 `python hearpitch.py ...`。

网页只监听本机地址 `127.0.0.1`。音频、项目、导出文件和非秘密配置默认保存在：

```text
Documents\HearPitchLocal
```

## 3. 命令行使用

在项目目录打开 PowerShell 或命令提示符：

```powershell
.\.venv\Scripts\activate
python hearpitch_cli.py transcribe "C:\\Music\\my_melody.wav"
```

默认命令行**不限时**。可传入标题、指定自己的数据目录，或强制用 pYIN 回退引擎：

```powershell
python hearpitch_cli.py transcribe "C:\\Music\\my_melody.wav" --title "练习旋律" --max-seconds 0
python hearpitch_cli.py transcribe "C:\\Music\\my_melody.wav" --no-basic-pitch
python hearpitch_cli.py transcribe "C:\\Music\\my_melody.wav" --preset conservative --engine pyin
python hearpitch_cli.py transcribe "C:\\Music\\my_melody.wav" --preset sensitive --sensitivity 78 --min-note-duration-ms 90 --min-confidence 50
python hearpitch_cli.py transcribe "C:\\Music\\vocal.wav" --engine rosvot-rmvpe --preset conservative
python hearpitch_cli.py --data-dir "D:\\HearPitchData" transcribe "C:\\Music\\my_melody.wav" --json
```

启动网页界面：

```powershell
python hearpitch_cli.py serve
```

如果需要关闭服务，请在该命令所在窗口按 `Ctrl+C`。

## 4. 可选：安装 Basic Pitch

基础安装默认优先 Basic Pitch，未安装时使用 pYIN 作为单旋律回退。面向歌声的 ROSVOT+RMVPE 为可选独立 CUDA 环境，不随基础安装自动安装。

在 Windows 中先启动一次 `start_windows.bat`，然后双击 `install_basic_pitch_windows.bat`。它会尝试安装 Spotify Basic Pitch。如果因 Python/TensorFlow 版本兼容失败，请保持 pYIN 回退模式，并在单独环境中按 Basic Pitch 官方兼容版本要求排查；软件本身不会把失败的模型安装伪装成可用状态。

## 4.1 HF3：识别灵敏度与进度

HF3 默认使用**保守预设**。上传卡片中的“识别灵敏度”范围为 0–100，数值高代表更愿意检出弱音和短音，**不代表准确率更高**。可展开“高级过滤参数”，分别调整最短音符时长、最低音高置信度和同音合并容差。ROSVOT 引擎会把灵敏度映射到其 note-boundary threshold；“最低音高置信度”仅适用于会返回置信度的引擎，ROSVOT 没有校准逐音置信度，因此显示为“—”。

上传后界面会显示本机任务的阶段、百分比、已运行时间和最近活动日志。命令窗口关闭前任务都在这台电脑内执行；关闭命令窗口会中止尚未完成的任务，但已完成项目不会丢失。

完整的数值设置、引擎选择与完整歌曲扩展路线请见 [识别精度与模型指南](ACCURACY_AND_MODEL_GUIDE.md)。

## 4.2 HF4：可选安装 ROSVOT + RMVPE CUDA 引擎

HF4 新增面向歌声的 ROSVOT 转谱引擎，RMVPE 作为其内部音高模块。此功能需要 NVIDIA CUDA、Python 3.9/3.10/3.11 x64 和网络连接；安装器会按 3.9 → 3.10 → 3.11 自动选择，并且**不会替换或改动 HearPitch 主虚拟环境**。安装前请关闭 HearPitch，确认 `py -0p` 与 `nvidia-smi` 可用，然后运行 `install_rosvot_cuda_windows.bat`。检查点约 557 MiB，另需为 CUDA PyTorch 和独立环境留出数 GB 磁盘空间。

安装后重新启动 HearPitch，在“转录引擎”中选择 **ROSVOT + RMVPE**。建议先用 20–60 秒录音测试。ROSVOT MIDI 没有校准过的逐音置信度，因此界面显示“—”，不以音量代替置信度。HF4.2 起，重新运行安装器可修复独立环境依赖而不重复下载模型。更完整的依赖、安装和故障排查见 [ROSVOT CUDA Windows 指南](ROSVOT_CUDA_WINDOWS.md)。

## 4.3 V261003A：声谱图校正工作台

打开任一转谱项目后，HearPitch 会在本机生成声谱图，并以音符块叠加现有引擎的候选结果。点击音符定位回放；拖动音符块左右边缘调整起止时间，拖动中部调整时间和音高。播放时按空格或点击“打拍”记录拍点，多个有效拍点可估算 BPM。编辑后点击“保存修订”，项目谱面和 MIDI、MusicXML、JSON 导出会一起更新。声谱图绘制和音频处理都留在本机。

当前声谱图显示 MIDI 36–84 的音高范围，MVP 不自动判定小节线，也不会让 LLM 自动修改音符；这些将作为后续迭代评估。音符仍应结合回放人工确认。

## 5. 可选：AI 谱面校对设置

AI 校对不是音频识别的一部分。它只读取候选谱中的音符事件、节奏、调性候选和置信度，提示需要人工回听的地方。它不自动修改谱面，也不上传原始音频。

在网页右上角点击“设置”，填写：配置名称、OpenAI 兼容 Base URL、模型名、Temperature 和 API Key。例如 OpenAI 兼容接口常用 Base URL 是：

```text
https://api.openai.com/v1
```

`profiles.json` 只保存名称、Base URL、模型名与 Temperature。API Key 使用 Windows Credential Manager 保存，保存后不会在界面或配置文件中显示。

也可使用命令行配置：

```powershell
python hearpitch_cli.py profile add --name "OpenAI-校对" --base-url "https://api.openai.com/v1" --model "gpt-5-mini"
python hearpitch_cli.py profile list
```

命令会安全地提示输入 API Key。不要把 API Key 放进代码、项目 JSON、截图或版本控制系统。

## 6. 项目文件结构

```text
Documents\HearPitchLocal\
  projects\
    20260924-...\
      source\        原始音频副本
      analysis\      本机分析音频
      exports\       MIDI / MusicXML / JSON
      score.json      可编辑的候选谱真源
      project.json    项目元数据
  profiles.json       非秘密 AI 配置
```

删除一个项目文件夹即可删除对应的音频、分析结果、候选谱和导出文件。删除 AI 配置会同时尝试从 Windows Credential Manager 删除对应 API Key。

## 7. 结果质量建议

为了得到更可用的结果，请优先使用单人清唱、口哨、笛子或其他一次只发一个音的乐器独奏。录音应避免伴奏、和弦、多人、强混响、嘈杂环境和明显的削波。若音符边界或节奏不自然，请使用网页编辑器拆分、合并或调整音符，最后点击“保存修订”再导出。

对于超过 300 秒的网页输入，请先勾选“长音频模式”。长音频将占用更多本机内存与时间；命令行默认不限时，但建议先用短片段验证音源质量。

## 8. 安全与隐私

HearPitch Local 默认只绑定 `127.0.0.1`。不配置 AI 校对时，音频和谱面均不会离开电脑。启用 AI 校对后，只有结构化音符事件会发送到用户自行配置的 OpenAI 兼容接口；请自行确认该提供商的隐私政策和费用。软件不会保存、回显或导出 API Key。
