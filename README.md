# 简单的 LangChain 代理

根据 [LangChain 官方概览](https://docs.langchain.com/oss/python/langchain/overview)，使用 LangChain 1.x 的 `create_agent` 实现一个中文命令行助手。模型根据问题决定调用工具，读取工具结果后生成回答。

## 功能与文件

- `multiply`：计算两个数字的乘积。
- `get_current_time`：返回运行程序的电脑的本地时间及 UTC 偏移，不查询指定城市的时间。
- `test.py`：代理定义和命令行入口（文件名沿用工作区原有文件，并非测试文件）。
- `requirements.txt`：项目依赖。
- `.env.example`：模型配置示例。

## 1. 安装依赖

需要 Python **3.10 或以上**。当前机器默认 `python` 是 3.9，已安装 3.11，因此建议在项目目录使用下面的 PowerShell 命令：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

直接使用虚拟环境里的 Python，无需运行激活脚本。在 IDE 中也可以将 Python 解释器设为 `.venv\Scripts\python.exe`。

## 2. 配置模型

```powershell
Copy-Item .env.example .env
```

编辑 `.env`，将 `OPENAI_API_KEY` 替换为自己的密钥：

```dotenv
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-4.1-mini
```

默认通过 `langchain-openai` 访问 OpenAI。也可以设置 `OPENAI_BASE_URL` 并替换模型名和密钥，使用兼容 OpenAI API 且支持 **tool calling（工具调用）** 的服务。模型需要在你的账户中可用，实际请求可能产生 API 费用。

程序始终读取 `test.py` 同目录的 `.env`；已有环境变量优先于 `.env`。`.env` 已加入 `.gitignore`，不要把真实密钥写进代码或 README。

## 3. 运行

使用默认问题“请计算 23 乘以 47，并告诉我当前时间”：

```powershell
.\.venv\Scripts\python.exe test.py
```

传入自己的问题：

```powershell
.\.venv\Scripts\python.exe test.py "12.5 乘以 8 是多少？"
.\.venv\Scripts\python.exe test.py "现在几点了？"
.\.venv\Scripts\python.exe test.py --help
```

第一个自定义问题的预期结果为 `100`，实际回答措辞由模型决定。每次运行是一次独立问答，不保存历史。

## 工作流程

1. `build_agent()` 读取配置，创建 `ChatOpenAI` 模型。
2. `@tool` 将带类型标注和说明的 Python 函数转换为模型可调用的工具。
3. `create_agent(model, tools, system_prompt)` 组合模型、工具和系统提示。
4. `agent.invoke()` 接收用户消息，自动执行“模型选择工具 → 执行工具 → 模型读取结果”的循环，最后输出回答。

可以仿照 `multiply` 新增工具函数，并加入 `create_agent` 的 `tools` 列表。程序配置了模型请求超时、失败重试和代理执行步数上限。

## 常见问题

- **缺少 API Key**：复制并填写 `.env`，不要保留示例占位值。
- **无法安装 LangChain 1.x**：检查是否误用了默认 Python 3.9，改用上面的 Python 3.11 虚拟环境。
- **找不到模块**：使用 `.venv\Scripts\python.exe` 安装依赖并运行，确保解释器一致。
- **认证、连接或模型错误**：核对密钥、账户可用模型、网络和可选的 `OPENAI_BASE_URL`。
- **工具调用失败**：确认使用的模型与兼容服务支持工具调用。

依赖限定在主版本范围内，便于安装兼容更新；该示例没有锁定所有间接依赖的精确版本。
