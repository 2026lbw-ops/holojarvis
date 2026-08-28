# HoloJarvis 集成验收与发布清单

## 当前结论（2026-08-28）

- **本地 Alpha：通过。** Python 3.12 自动检查、42 项单元测试、全新目录安装、DeepSeek 云模型、本地工具调用和 Windows SAPI 朗读均通过。
- **发布候选：通过。** `v1.1.0` 最终提交已完成，自动与人工设备验收均通过。
- **公开稳定版：待发布。** 本轮没有推送、创建标签或发布远程 Release。

## 自动验收

在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe scripts\release_check.py
```

脚本必须全部通过：

- [x] Python 为 3.12。
- [x] `mcp.json` 可解析。
- [x] `jarvis/` 与 `tests/` 可编译。
- [x] 从 `tests/` 发现并运行完整单元测试。
- [x] `pip check` 无损坏依赖。
- [x] `git diff --check` 无空白错误。
- [x] Git 未跟踪密钥、SQLite、工作区或音频文件。
- [x] 使用本机回环占位地址完成无云调用启动自检。
- [x] `/skills`、`/task`、`/diff`、`/undo` 本地命令 smoke 通过。

## 人工设备验收

发布候选提交必须逐项确认：

- [x] 在全新目录用 Python 3.12 创建 `.venv` 并按 README 安装成功。
- [x] 真实 DeepSeek 云模型完成普通对话和本地 `get_time` 工具调用。
- [x] 麦克风唤醒、连续对话、静默回待机正常（本机 `whisper-base` + AB13X 实测：唤醒词识别为“小维斯”后可处理；未重复唤醒词的下一轮命令可处理；静默 35 秒后同一句只识别、不执行）。
- [x] Windows SAPI 能通过指定 Conexant 输出设备朗读。
- [x] 桌宠、`--holo`、`--no-pet`、`--text` 四种入口至少各启动一次（`--no-pet`、`--text` 已通过；本轮补齐桌宠与 `--holo` 启动验收）。
- [x] 任务、进度、提醒在重启后保留；到期提醒只显示一次（真实 `tasks.db` 验证）。
- [x] 记忆分类、云发送范围、导出和清空确认符合预期（导出/清空真实文件验证；分类与云发送范围由单元测试覆盖）。
- [x] Diff 接受、冲突保护、撤销新建与恢复旧文件均用真实文件验证（真实文件 accept/undo 已验证；冲突保护由单元测试覆盖）。
- [x] Playwright 使用隔离 profile；真实浏览器使用临时 `playwright_chromiumdev_profile-*`，下载仅落在 `workspace/browser-output/`。
- [x] 上传、网页点击、提交和付款已在本地受控页面分别验证“确认执行”与“取消”；取消时服务器计数不变，确认后各执行一次。
- [x] MCP 启动日志和 `/skills mcp` 展示的权限与 `mcp.json` 一致（filesystem 真实接入：允许 9/需确认 0/拒绝 5；已修复 Tool 字段兼容问题）。

## 发布前操作

- [x] 审阅全部 Diff；未发现个人路径、密钥、测试数据库或生成文件；已修复跨模型轮确认覆盖和 TTS 临时文件残留。
- [x] 更新版本号和变更日志；`jarvis.__version__` 为 `1.1.0`，详见 `CHANGELOG.md`。
- [x] `git status --short` 为空，并在最终提交上重新运行自动验收。
- [ ] 从最终提交创建标签，再生成 Release；不要从脏工作树发布。
- [ ] Release 说明列出 Python 3.12、Windows/macOS 支持范围、默认关闭的危险工具与已知限制。

## 已知首版限制

- 定时提醒只在 Jarvis 文字模式运行时主动检查，不是系统级后台通知。
- 浏览器所有点击均要求确认，安全但交互较繁琐。
- 不支持 redo、多文件原子 Diff、重复提醒、MCP 权限通配符或独立 `SKILL.md` 安装器。
- 真实硬件、模型和第三方服务结果无法由离线单元测试替代。
- 老旧 AB13X USB 音频使用 `soundcard` 兼容后端，连续采集块为 5 秒，响应延迟较高；`whisper-base` 在背景人声下可能误识别。
