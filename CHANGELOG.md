# Changelog

## 1.1.2 - 2026-08-28

### Changed

- Windows 桌宠启动时启用 DPI 感知，减少高缩放显示器上的二次缩放发糊。
- 默认语音平衡调整为 Whisper base、beam 3、0.35 秒静音收尾和 384 Token 回复。
- 对泛化提问只给三个具体可执行选项，避免冗长能力罗列。

## 1.1.1 - 2026-08-28

### Fixed

- 将 README 和贡献指南中的克隆地址更新为 `2026lbw-ops/holojarvis`。

## 1.1.0 - 2026-08-28

### Added

- 持久记忆分类与云端发送范围控制，任务、进度和一次性提醒。
- 文件 Diff 提案、接受、拒绝与安全撤销。
- 受权限清单约束的 MCP/Playwright 集成、隔离浏览器 profile 与工作区下载目录。
- 桌宠与 `--holo` 全息入口。

### Changed

- Windows 音频设备、语音识别和流式回复的兼容性；支持 DeepSeek 官方云端配置。

### Fixed

- 高风险操作待确认后立即停止模型循环，防止确认目标被后续工具调用覆盖。
- 清理 Windows SAPI 和 GPT-SoVITS 生成的临时音频文件。
