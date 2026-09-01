# JobAgent

本地优先的编程 Agent 运行时（单 Agent、单进程、最小闭环），用于学习与实习展示。
通过 OpenAI 兼容接口接入大模型，用 ReAct 循环驱动工具完成任务，并把每次运行落盘为可回放的 trace。

## 快速开始

```powershell
# 1. 安装依赖（含测试）
uv sync --extra dev

# 2. 配置模型：复制 .env.example 为 .env，填写 LLM_API_KEY / LLM_MODEL_ID / LLM_BASE_URL

# 3. 跑测试
.venv\Scripts\python.exe -m pytest -q

# 4. 交互式 CLI
.venv\Scripts\python.exe -m app.cli

# 5. 单轮任务（执行一次即退出）
.venv\Scripts\python.exe -m app.one_shot -p "找出 tools/builtin 下所有 .py 文件"
```

## 里程碑

- M1 能对话：配置 + 消息 + 模型封装
- M2 能调工具：工具注册中心 + Glob / Read
- M3 闭环跑通：ReAct 主循环 + Grep / Edit + 交互 CLI
- M4 变得可靠：读后写三件套 + JSONL trace / transcript + Bash 兜底 + 单轮入口

## 目录

- `core/`：基础层，配置 / 消息 / 模型封装
- `runtime/`：ReAct 主循环与运行状态
- `tools/`：Glob / Grep / Read / Edit / Bash
- `memory/`：JSONL trace 与会话记录
- `app/`：交互 CLI 与单轮入口
- `demo/`：比单元测试更完整的任务演示
- `tests/`：pytest 单元测试