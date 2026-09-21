# LangChain AI 群聊与命令行助手

支持多个用户进入同一群实时聊天，通过 `@AI` 邀请 AI 结合群内上下文回答。原有命令行模式仍然可用。

## Docker 启动

安装并启动 Docker Desktop（使用 Linux containers），在项目目录配置好 `.env` 后执行：

```powershell
docker compose up -d --build
docker compose ps
docker compose logs --tail 50 chat
```

打开 http://127.0.0.1:8001 。Docker 使用 8001 端口，避免与原有本地 8000 服务冲突。此 Compose 默认只允许本机访问；公网部署还需配置平台入口或 HTTPS 反向代理。

- 镜像使用 Python 3.11，以非 root 用户、单个 Uvicorn worker 运行。
- `.env` 通过 Compose 注入环境变量，不复制进镜像。构建目录采用 `.dockerignore` 白名单，也排除本地数据库、虚拟环境、Git 和日志。
- 数据库保存在 Docker 命名卷 `chat-data` 的 `/app/data` 中。首次启动是独立的新数据库，不会自动导入本地 `data/chat.db`。重建容器和普通 `docker compose down` 保留数据；不要使用 `docker compose down -v`，它会删除数据卷。更换项目名也会使用不同的默认命名卷。
- `SOFTWARE_GUIDE.md` 只读挂载，修改本地说明可立即用于后续模型调用。
- `restart: unless-stopped` 在异常退出或 Docker 引擎重启后恢复服务；手动停止的容器不会自动恢复。Docker Desktop 必须运行，电脑关机后无法提供服务。健康检查用于查看状态，不会单独触发容器重启。
- 容器中的 `127.0.0.1` 是容器自身。若 `.env` 的 `MODEL_PROXY_URL` 指向 Windows 主机的代理，Docker Desktop 下需改用 `http://host.docker.internal:实际端口`，并确保代理允许容器访问；可以直连时不必配置代理。

常用命令：

```powershell
# 更新代码或 .env 后重新构建并启动
docker compose up -d --build
# 跟踪日志（Ctrl+C 只退出日志查看）
docker compose logs -f chat
# 停止服务，保留数据
docker compose down
```

仅制作镜像供云平台部署：

```powershell
docker build -t together-chat:latest .
```

云平台使用该镜像时，将流量转发到容器 8000 端口、配置环境变量、挂载持久磁盘到 `/app/data`（运行用户 UID/GID 为 10001，需有写权限），并保持一个实例。云平台也可直接从此仓库的 Dockerfile 构建。

## 启动网页版群聊

使用 Python **3.11 或以上**。先按下文创建虚拟环境并配置 `.env`，然后运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8000
```

打开 **http://127.0.0.1:8000**，输入昵称和群名。浏览器会记住最近使用的身份；模拟两位用户时，可使用普通窗口和无痕窗口，或在新标签页点击“更换身份”。同名群共享聊天记录。

试用流程：小王发送“周六去爬山”，小李发送“四点前回来”，然后发送“@AI 总结一下我们的计划”。群成员都能看到 AI 回答。普通消息不调用模型；未配置模型时普通群聊也可使用，`@AI` 会显示失败提示。

- 消息存储在 `data/chat.db`，刷新或重启后保留；该目录不提交到 Git。
- 每次进入展示最近 100 条消息，可点击“加载更早消息”向前翻页；AI 使用当前群截至触发消息的最近 40 条消息，包含发送者身份与被引用的消息摘要。
- 同群 AI 请求依次处理，等待队列最多 10 条；不同群分别处理。单次 AI 任务最多 90 秒，失败不影响普通聊天。
- 身份令牌保存在标签页的 `sessionStorage`，并在 `localStorage` 保存最近身份；关闭浏览器后也可恢复。“我的群聊”列出当前身份加入的群，切群无需重输昵称。“更换身份”会清除浏览器记住的身份；不同身份的群列表不合并。清理网站数据、换浏览器或更换隧道域名后无法自动恢复；聊天记录仍在服务端，同名群可重新加入。
- 这是本机公开群原型：知道群名即可加入，不含账号密码、私密群邀请、生产环境限流和多进程广播。使用单个 Uvicorn worker；暂不直接部署到公网。
- 运行中的 AI 任务在服务重启时不会恢复，需重新 `@AI`。

### 引用、图片和软件说明

- 点击消息旁“引用”，输入框上方显示引用内容；可点击 × 取消。发送后显示原发言者和摘要，点击可定位已加载的原消息。引用较早的消息提问时，AI 也会收到最多 500 字的引用摘要。
- 点击“图片”选择 JPEG、PNG 或 WebP（最大 5 MB、2000 万像素），上传完成后点击发送。可单发图片或附带文字。服务端校验并转成最长边 2048 像素的 JPEG，移除 EXIF；群成员可点击图片放大。图片随数据库持久保存，未发送附件超过一天后在下次上传时清理。
- **当前 AI 只读取文字，不分析图片内容。** 图片发送是群成员间的聊天能力，后续接入支持视觉的模型后可扩展识图。
- 发送 `@AI 介绍一下这个软件`，AI 根据项目中的 `SOFTWARE_GUIDE.md` 回答。每次模型调用前自动读取最新说明并加入上下文，不依赖模型主动选择工具；该功能会调用模型。没有独立的介绍按钮，`/介绍`、`/help` 作为普通消息处理。
- 移动端保留键盘正常弹出行为，输入字号至少 16px，聊天容器跟随可见窗口高度。手机回车换行，点击“发送”发送；电脑仍支持 Enter 发送、Shift + Enter 换行。

### 文件结构

| 文件 | 用途 |
| --- | --- |
| `agent.py` | 共用 Agent、模型配置、乘法和时间工具 |
| `test.py` | 保留的命令行入口 |
| `server.py` | FastAPI、WebSocket、按群排队的 AI 回复 |
| `storage.py` | SQLite 身份、群成员、消息持久化 |
| `image_upload.py` | 图片校验、缩放、重新编码 |
| `static/` | 原生 HTML / CSS / JavaScript 聊天网页 |
| `tests/test_group_chat.py` | 不调用真实模型的群聊集成测试 |

### 接口

1. `POST /api/sessions`：`{"nickname":"小王"}`，返回身份和令牌。
2. `POST /api/rooms`：`{"name":"周末计划"}`，用 `Authorization: Bearer <token>` 创建或加入群。
3. `GET /api/rooms/{id}/messages`：相同身份头，读取群最近 100 条消息；仅成员可读。
4. `WS /ws/rooms/{id}`：连接后 10 秒内发送 `{"token":"..."}`，收到 `history` 后发送 `{"content":"你好"}`。服务端推送 `message`、`ai_status` 或 `error` 事件。身份从令牌解析，客户端不能指定发送者。
5. `GET /api/rooms`：当前身份加入的群列表。
6. `GET /api/rooms/{id}/messages?before=<message_id>`：加载指定消息之前的最多 100 条消息。
7. `POST /api/rooms/{id}/images`：请求体直接为图片文件字节，返回附件 `id`；`GET /api/rooms/{id}/images/{image_id}` 下载图片。这两个接口均要求身份头和群成员权限。

WebSocket 发送消息还可带 `reply_to`（本群消息 ID）和 `image_id`（自己在本群上传的图片 ID）；文字或图片至少提供一项。消息响应包含服务端生成的 `quote` 摘要。

运行离线测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test tests/frontend.test.cjs
```

服务启动后可运行双用户网络验证（添加 `--live-ai` 会真实调用模型并可能产生 API 费用）：

```powershell
.\.venv\Scripts\python.exe scripts/smoke_chat.py
.\.venv\Scripts\python.exe scripts/smoke_chat.py --live-ai
.\.venv\Scripts\python.exe scripts/smoke_features.py
```

验证范围与结果见 [群聊体验升级](docs/requirements/07-chat-improvements.md)。浏览器视觉与手机软键盘验收仍需真机验证。

以下为原有命令行助手的使用说明。

这是一个使用 LangChain 1.x `create_agent` 实现的中文命令行助手。模型会根据问题选择工具，读取工具结果后生成回答。项目默认通过 OpenAI 兼容接口调用模型，也可配置 Moonshot/Kimi 等服务。

## 功能与文件

- `multiply`：计算两个数字的乘积。
- `get_current_time`：返回运行程序的电脑的本地时间及 UTC 偏移，不查询指定城市的时间。
- 多轮对话：保留本次会话的上下文，支持追问、清空历史和退出。
- `agent.py`：代理定义与工具；`test.py`：命令行入口（文件名沿用工作区原有文件，并非测试文件）。
- `requirements.txt`：项目依赖。
- `.env.example`：模型配置示例。
- `docs/requirements/`：[功能路线与需求记录](docs/requirements/README.md)，包含各阶段状态、开发范围、验收标准及新增需求模板。

## 1. 安装依赖

需要 Python **3.11 或以上**。在项目目录使用 PowerShell：

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

编辑 `.env`，填写服务商提供的 API Key、模型名称和接口地址。OpenAI 官方 API 示例：

```dotenv
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-4.1-mini
```

Moonshot/Kimi 示例：

```dotenv
OPENAI_API_KEY=你的Moonshot_API密钥
OPENAI_MODEL=kimi-k3
OPENAI_BASE_URL=https://api.moonshot.cn/v1
```

`OPENAI_MODEL` 必须是服务商提供且你的账号有权限使用的模型；模型还需要支持 **tool calling（工具调用）**。使用第三方服务时必须填写该服务的 `OPENAI_BASE_URL`。模型请求可能产生 API 费用。

本次先保留现有模型，提供配置入口；后续切换 GPT6 时需要填写服务商实际支持的准确模型 ID、匹配的 API 地址和密钥。未提供这些配置前不替换模型，也不承诺一定提速。修改配置后重启服务。

### 后端模型代理

用户的浏览器只访问群聊后端，不直接连接模型服务。若运行后端的电脑需要通过代理访问模型，在 `.env` 加上实际的代理地址，例如：

```dotenv
MODEL_PROXY_URL=http://127.0.0.1:7890
# 或 MODEL_PROXY_URL=socks5://127.0.0.1:7891
```

端口以你的代理软件设置为准；无需为了此功能给用户浏览器配置代理。此项只作用于模型请求，不修改系统代理，也不启动代理软件。留空时沿用现有连接方式（仍可能受系统环境代理变量影响）。`OPENAI_BASE_URL` 是模型接口地址，`MODEL_PROXY_URL` 是网络代理地址，两者不能混用。

代理只解决后端到模型的连接；朋友能否打开网站，还取决于部署地址、校园网或隧道本身的可达性，不能保证所有网络都可访问。

程序始终读取 `test.py` 同目录的 `.env`；已有环境变量优先于 `.env`。`.env` 已加入 `.gitignore`，不会被提交。不要把真实密钥写进代码、README、聊天记录或 GitHub；如果密钥意外泄露，请立即在服务商控制台撤销并重新生成。

## 3. 运行

不传参数时进入多轮对话：

```powershell
.\.venv\Scripts\python.exe test.py
```

例如（模型的实际措辞可能不同）：

```text
你：计算 23 乘以 47
助手：结果是 1081。
你：再乘以 2
助手：结果是 2162。
你：exit
对话已结束。
```

输入 `exit` 或 `quit` 退出，也可以按 `Ctrl+C`。输入 `/clear` 清空上下文，开始新的话题。空白输入会被忽略；请求失败时保留此前成功的对话，可以继续提问。

历史仅保存在本次进程内存中，包含用户消息、模型回复和工具结果；退出后不会保存到文件。对话越长，请求携带的上下文越多，可能增加费用或达到模型上下文上限，可用 `/clear` 清空。

也可以直接传入问题，单次回答后退出：

```powershell
.\.venv\Scripts\python.exe test.py "12.5 乘以 8 是多少？"
.\.venv\Scripts\python.exe test.py "现在几点了？"
.\.venv\Scripts\python.exe test.py --help
```

第一个自定义问题的预期结果为 `100`，实际回答措辞由模型决定。

## 工作流程

1. `build_agent()` 读取配置，创建 `ChatOpenAI` 模型。
2. `@tool` 将带类型标注和说明的 Python 函数转换为模型可调用的工具。
3. `create_agent(model, tools, system_prompt)` 组合模型、工具和系统提示。
4. `agent.invoke()` 接收用户消息，自动执行“模型选择工具 → 执行工具 → 模型读取结果”的循环，最后输出回答。
5. 多轮模式下，`run_chat()` 将完整消息历史传入下一次调用，使模型能理解追问。

可以仿照 `multiply` 新增工具函数，并加入 `create_agent` 的 `tools` 列表。程序配置了模型请求超时、失败重试和代理执行步数上限。

## 常见问题

- **缺少 API Key**：复制并填写 `.env`，不要保留示例占位值。
- **无法安装 LangChain 1.x**：检查是否误用了默认 Python 3.9，改用上面的 Python 3.11 虚拟环境。
- **找不到模块**：使用 `.venv\Scripts\python.exe` 安装依赖并运行，确保解释器一致。
- **认证错误（401）**：确认 API Key 来自当前 `OPENAI_BASE_URL` 对应的平台，未过期、未禁用，且没有多余空格或引号。
- **服务不可用（503）**：服务商暂时没有可用的上游模型，或模型与请求格式不兼容；确认模型名称和平台文档。
- **认证、连接或模型错误**：核对密钥、账户可用模型、网络和 `OPENAI_BASE_URL`。
- **工具调用失败**：确认使用的模型与兼容服务支持工具调用。

依赖限定在主版本范围内，便于安装兼容更新；该示例没有锁定所有间接依赖的精确版本。
