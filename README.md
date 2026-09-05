# 专项多 Agent 工作流

这个项目使用 LangGraph 编排一个有明确权限边界的软件开发流程：

1. 规划 Agent 将需求转换为实施步骤和验收标准。
2. Codex CLI 在独立 Git worktree 中修改代码和测试。
3. 本地测试节点运行仓库的真实测试命令。
4. Claude 文档 Agent 根据最终差异和测试结果更新文档。
5. 独立 Reviewer 检查正确性、回归、测试和文档。
6. 工作流暂停，等待人工检查差异并批准。
7. 批准后只在隔离分支创建提交，不自动合并或推送。

## 当前默认分工

| 职责 | 默认模型/执行器 |
| --- | --- |
| 需求规划 | `gpt-5.6-terra` |
| 编写代码 | Codex CLI + `gpt-5.6-sol`（`xhigh`） |
| 编写文档 | `claude-sonnet-4-6` |
| 独立审查 | `gpt-5.6-luna` |
| 测试与 Git | 本地确定性程序，不交给模型 |

这些默认值来自当前中转站公开的模型列表和角色基准测试，可通过 `.env` 修改。基准中，`gpt-5.6-terra` 在规划任务得到 8/8，`claude-sonnet-4-6` 在文档忠实度任务得到 8/8，`gpt-5.6-luna` 在安全审查任务得到 8/8 且是同分模型中最快的。中转站可能调整别名背后的路由，因此这不是永久不变的模型排名。

## 安全设计

- 工作流默认读取现有 `~/.codex/auth.json` 和 `~/.codex/config.toml`，不会复制或显示密钥。
- 每个任务创建独立的 `agent/<任务ID>` 分支和 Git worktree。
- Codex 使用 `workspace-write` 沙箱，并被明确禁止提交、合并和推送。
- Codex 子进程使用独立 `CODEX_HOME`，不加载全局插件和 Skills；优先用硬链接复用现有 `~/.codex/auth.json`，不会复制或打印密钥。没有现有登录文件时才通过子进程环境变量传入。
- Codex 对文档文件的修改会在进入下一节点前自动撤销。
- 文档 Agent 只能写 Markdown、MDX、RST 或 `docs/` 下的文件。
- Reviewer 没有文件写入工具。
- 测试和审查失败最多重试 `MAX_RETRIES` 次。
- 人工批准前不会创建提交；批准后仍不会合并或推送。

第三方中转站理论上能看到发送给模型的需求、代码差异和文档。不要用它处理密码、生产数据或不能交给第三方的私有代码。

## 安装

在 PowerShell 中执行：

```powershell
Set-Location "本项目目录"
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
.\.venv\Scripts\specialist-workflow.exe doctor
```

`doctor` 只显示 Base URL、模型和工具路径，密钥只显示为 `available (hidden)`。

如果不想复用当前 Codex 登录，可以复制 `.env.example` 为 `.env`，再填写：

```dotenv
AI_API_KEY=完整密钥
AI_BASE_URL=https://你的中转站/v1
AI_API_MODE=auto
```

`.env` 已被 `.gitignore` 忽略。不要把真实密钥写入 `.env.example`。

## 运行第一个任务

目标项目必须是一个状态干净、至少有一个提交的 Git 仓库：

```powershell
.\.venv\Scripts\specialist-workflow.exe run `
  --repo "D:\code\my-project" `
  --request "为登录接口增加速率限制并补充测试和 README" `
  --test-command "python -m pytest"
```

也可以使用包装脚本：

```powershell
.\run.ps1 `
  -Repo "D:\code\my-project" `
  -Request "为登录接口增加速率限制并补充测试和 README" `
  -TestCommand "python -m pytest"
```

如果省略测试命令，工作流会按以下文件自动判断：

- Python：`python -m pytest`
- Node.js：`npm.cmd test -- --run`
- Rust：`cargo test`
- Go：`go test ./...`

## 人工审批

执行完成后，命令行会显示 `Thread ID` 并暂停。先查看状态和完整差异：

```powershell
.\.venv\Scripts\specialist-workflow.exe status <Thread-ID>
.\.venv\Scripts\specialist-workflow.exe inspect <Thread-ID>
```

确认后批准：

```powershell
.\.venv\Scripts\specialist-workflow.exe resume <Thread-ID> --approve
```

拒绝：

```powershell
.\.venv\Scripts\specialist-workflow.exe resume <Thread-ID> --reject
```

批准只会在 `agent/<任务ID>` 分支创建提交。你仍需自行审查和合并：

```powershell
git log --oneline --all --decorate -10
git merge agent/<任务ID>
```

## 修改模型

在项目根目录创建 `.env`：

```dotenv
PLANNER_MODEL=gpt-5.6-terra
CODE_MODEL=gpt-5.6-sol
CODE_REASONING_EFFORT=xhigh
DOCS_MODEL=claude-sonnet-4-6
REVIEW_MODEL=gpt-5.6-luna
MAX_RETRIES=2
```

建议同时明确每个角色的协议，避免中转站对不支持的端点进行长时间重试：

```dotenv
PLANNER_API_MODE=responses
DOCS_API_MODE=chat_completions
REVIEW_API_MODE=responses
```

模型 ID 必须与中转站 `/v1/models` 返回的 ID 完全一致。默认的 `auto` 会优先尝试 Responses API，并在当前模型不支持时自动切换到 Chat Completions。也可以强制使用 Chat Completions：

```dotenv
AI_API_MODE=chat_completions
```

## 注意事项

- 源仓库有未提交修改时，工作流会拒绝启动，以免混入用户改动。
- Agent 生成的代码仍可能有错误，人工审批不能省略。
- 首次建议在一个小型测试仓库上运行，确认中转站的各模型至少兼容 Responses API 或 Chat Completions。
- 工作流检查点位于 `~/.specialist-agent-workflow/checkpoints.sqlite`。
- Windows 隔离 worktree 位于短路径 `C:\agent-runtime\worktrees\`，避免 Codex 沙箱触发路径长度限制。
- 独立 Codex 运行目录位于 `C:\agent-runtime\codex-home\`，其中不保存 API Key。
- Windows 沙箱使用 `unelevated`（受限令牌）模式。当前用户目录顶层项目较多，`elevated` 模式会生成超过 Windows 命令行上限的初始化载荷并弹出“参数错误”。
