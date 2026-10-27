# USB Mouse Fusion

基于 RP2040 的 USB 鼠标输入融合项目：接收实体鼠标输入，并探索将上位机发送的增量指令融合后，以标准 USB HID Mouse 输出给目标电脑。本仓库是用于展示的源码摘录，项目仍在开发和验证中。

## 系统结构

```text
鼠标 / 2.4G 接收器 → PIO USB Host ┐
                                  ├→ RP2040 → 原生 USB HID → 目标电脑
上位机 / Studio → USB 转隔离串口 ─┘
```

- 固件：C/C++、RP2040、PIO USB Host、USB HID、UART、轨迹与按键状态处理。
- 上位机：Python、Tkinter、Pillow；Studio 用于编辑轨迹、配置模式并与设备通信。
- 硬件：自设计 RP2040 板卡，包含 Host、Device、隔离串口及保护电路。

## 进展与验证范围

- 用户反馈目前使用 Mouse Fusion Studio V2 和 0.6.0-flash 正常；其截图显示实机已连接、支持 Flash 保存及开机恢复成功。截图显示状态，不代表对每项功能进行独立测试。
- 已有板卡完成基础鼠标透传；另一块 0.6.0 板卡曾验证配置保存、断电恢复及指定轨迹运行。不同板卡和功能不能以此类推。
- 本仓库固件源码包含后续 0.6.3 平滑控制候选，**不是当前使用的 0.6.0 固件的精确源码快照**。0.6.3 尚未部署验收；Studio 源码与截图中运行的程序也尚未逐文件核对一致。
- AI 控制的完整运动、释放和断连恢复，以及 1 ms / 1000 Hz 实机性能尚待验证。离线测试不代替硬件实测。

## 目录

```text
software/studio/                  Studio 编辑界面和示例
software/src/mouse_fusion/        上位机 SDK 与本地通信服务
software/desktop/                Windows 桌面入口相关源码
firmware/mouse_passthrough/      RP2040 固件和部分第三方组件
firmware/rp2040/vendor/          PIO USB 组件及其许可
firmware/cdc_alive/boards/       板级配置
```

## 本地查看 Studio

需要 Python 3.10 或以上、Tkinter 和 Pillow。安装依赖后，在仓库根目录执行：

```powershell
python -m pip install -r software/requirements-studio.txt
python software/run_studio.py --project software/studio/examples/studio-v2/studio-v2.mfusion
```

该入口可用于查看和编辑界面；实机通信还需要正确的设备、串口和设备标识。此展示摘录尚未完成全新环境及真实设备连接验证。

## 固件构建说明

固件使用 Pico SDK 2.2.0、ARM 工具链、TinyUSB 与 PIO USB，并依赖项目中定制的 USB Host 集成。本展示摘录未包含 SDK、工具链和板卡 USB 身份配置，也未验证从零完整编译，暂不能直接用于烧录。USB VID/PID 的授权和设备身份应由使用者自行核实。

本仓库不提供与特定板卡绑定的固件镜像。实际烧录和板级验证请使用适配具体硬件并经过核对的构建产物。

## 第三方组件与许可

PIO USB 和 HID report parser 的原始许可随各自源码保留。项目自有代码尚未选定开源许可证；在权利人确定许可之前，不应默认获得再分发或商用授权。
