# ChatLens

## ✨ 功能特性

- 🔓 **数据解密** — 自动调用 chatlog 解密微信本地数据库
- 📅 **日期范围过滤** — 数据源加载、报告生成、统计分析全链路支持 `start_date` / `end_date`，SQL 层过滤避免加载全量数据
- 📊 **统计分析** — 消息概览、成员排名、时间分布、话题聚类
- 🤖 **AI 智能分析** — 用户称号/画像、金句识别、聊天质量锐评（支持 API Key / Ollama / 规则分析三级降级）
- ⚠️ **AI 失败原因透传** — 限流/超时/认证错误等异常分类并展示给用户，不再静默失败
- 📝 **报告生成** — HTML / JPG / PNG 多格式输出，3 套主题模板（手账风 / 经典暖橙 / 黑客风）
- 📊 **SVG 图表自适应** — 24h 柱状图 1 小时粒度，长周期趋势图 label 自动抽稀防重叠
- ⏰ **定时任务** — 定时解密并分析指定群聊，支持持久化与执行历史
- 🌐 **Web 界面** — 暖色设计，响应式布局，HTML 报告在线预览 + AI 失败警告横幅
- 🖥️ **CLI 命令行** — 完整的命令行工具，支持脚本化操作
- 🔌 **MCP 服务器** — 可在 Trae / Claude Code / Cursor 等 IDE 中直接调用分析功能
- 🧩 **多平台架构** — `MessageProvider` 协议抽象数据源，核心引擎与平台完全解耦
- 🧪 **测试覆盖** — 完整 pytest 套件（0 失败）+ perf-scan 性能反模式扫描

---

[![CI](https://github.com/MeetMX-ai/chatlens/actions/workflows/ci.yml/badge.svg)](https://github.com/MeetMX-ai/chatlens/actions/workflows/ci.yml)

> 🔍 **AI-powered lens into your WeChat group chats.**
> 基于 chatlog 解密数据，对微信群聊进行智能分析并生成精美报告。

支持 **Web 界面**、**命令行 (CLI)**、**MCP 服务器** 三种使用方式。

## 📋 前置要求

- **Python** ≥ 3.10
- **chatlog_alpha** — 微信数据库解密工具（需自行下载放置于项目根目录）
- **微信** 需在本地运行（用于获取数据库密钥）

## 🚀 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/MeetMX-ai/chatlens.git
cd chatlens
```

### 2. 下载 chatlog_alpha

从 [chatlog_alpha Releases](https://github.com/CJYKK/chatlog_backup/releases) 下载对应平台的可执行文件，放置于项目根目录的 `chatlog_alpha/` 下。

目录结构应如下：

```
chatlens/
├── chatlog_alpha/
│   ├── chatlog.exe          # Windows
│   ├── lib/
│   │   └── windows_x64/
│   │       └── wx_key.dll
│   └── ...
├── chatlens/
├── config/
├── reports/
└── ...
```

### 3. 一键启动（Windows）

双击 `start.bat`，脚本将自动：
- 创建 Python 虚拟环境（如不存在）
- 安装依赖
- 启动 chatlog 解密
- 启动 Web 服务器
- 打开浏览器访问 http://localhost:8080

### 手动安装

```bash
# 创建虚拟环境
python -m venv .venv

# 激活虚拟环境
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 启动服务
python -m chatlens
```

## 🖥️ CLI 使用

安装后可使用 `wxcli` 命令：

```bash
# 安装 CLI 入口
pip install -e .

# 查看帮助
wxcli --help

# 列出群聊
wxcli groups

# 列出 chatlog 聊天对象
wxcli chatlog talkers

# 从 chatlog 加载群聊消息
wxcli chatlog load <chatroom_id>@chatroom

# 解密微信数据库
wxcli chatlog decrypt

# 分析群聊并生成报告
wxcli analyze "<group_name>" --theme scrapbook --format html
wxcli analyze "<group_name>" --start-date 2026-01-01 --end-date 2026-05-31

# 定时任务管理
wxcli schedule create "<group_name>" --hour 9 --minute 0
wxcli schedule list
wxcli schedule trigger <task_id>
wxcli schedule delete <task_id>

# 报告管理
wxcli reports
wxcli report-delete <filename>

# 系统状态
wxcli status
wxcli health
```

也可以使用 `python -m chatlens` 代替 `wxcli`。

## 🔌 MCP 服务器

在 IDE 的 MCP 配置中添加：

```json
{
  "mcpServers": {
    "chatlens": {
      "command": "python",
      "args": ["-m", "chatlens.mcp_server"],
      "cwd": "/path/to/chatlens"
    }
  }
}
```

连接后可在 IDE 中直接使用以下 MCP 工具：

| 工具 | 说明 |
|------|------|
| `list_groups` | 列出已加载的群聊 |
| `list_chatlog_talkers` | 列出 chatlog 所有聊天对象 |
| `load_chatlog_data` | 从 chatlog 加载群聊消息 |
| `analyze_group` | 统计分析群聊 |
| `get_messages_for_ai` | 获取消息供 AI 分析 |
| `ai_analyze_group` | AI 智能分析 |
| `generate_report_image` | 生成报告图片 |
| `generate_report_pdf` | 生成报告 PDF |
| `get_user_titles` | 获取用户称号 |
| `get_golden_quotes` | 获取金句 |
| `get_chat_quality` | 获取质量锐评 |
| `search_messages` | 搜索消息 |
| `check_pending_tasks` | 检查 IDE 待分析任务 |
| `submit_ide_analysis` | 提交 IDE 分析结果 |
| `refresh_chatlog_data` | 刷新 chatlog 数据 |
| `schedule_task_create` | 创建定时任务 |
| `schedule_task_list` | 列出定时任务 |
| `schedule_task_trigger` | 触发定时任务 |
| `schedule_task_delete` | 删除定时任务 |

## 📁 项目结构

```
chatlens/
├── chatlens/
│   ├── __init__.py              # 版本声明
│   ├── __main__.py              # python -m 入口
│   ├── main.py                  # Web 服务器入口
│   ├── cli.py                   # CLI 入口
│   ├── core/                    # 核心引擎（平台无关）
│   │   ├── __init__.py          # GroupAnalysis 主类 + PluginRegistry
│   │   ├── models.py            # ChatMessage 数据模型
│   │   ├── providers.py         # MessageProvider 协议 + ProviderRegistry
│   │   ├── analyzer.py          # 统计分析器
│   │   ├── ai_analyzer.py       # AI 分析器（API / Ollama / 规则三级降级）
│   │   ├── chatlog_bridge.py    # 微信数据库桥接
│   │   ├── _rule_engine.py      # 规则引擎
│   │   ├── _analysis_data.py    # 分析数据处理
│   │   ├── _chatlog_runtime.py  # chatlog 运行时管理
│   │   └── _chatlens_methods.py
│   ├── plugins/                 # 插件层
│   │   ├── web/                 # Web 界面插件
│   │   │   ├── api_server.py    # Web API 服务
│   │   │   ├── http_handler.py  # HTTP 请求处理
│   │   │   ├── handler.py       # 服务器启动
│   │   │   └── ide_tasks.py     # IDE 任务队列
│   │   ├── cli/                 # CLI 命令行插件
│   │   │   └── commands.py      # wxcli 命令实现
│   │   ├── mcp/                 # MCP 服务器插件
│   │   │   └── mcp_server.py    # MCP 工具定义
│   │   ├── report/              # 报告生成插件
│   │   │   ├── engine.py        # 报告引擎
│   │   │   ├── image_report.py  # HTML→图片
│   │   │   ├── pdf_report.py    # PDF 生成
│   │   │   ├── svg_charts.py    # SVG 图表
│   │   │   └── template_engine.py  # Jinja2 模板
│   │   └── schedule/            # 定时任务插件
│   │       ├── scheduler.py     # 调度器核心
│   │       └── scheduler_impl.py
│   ├── report_templates/        # 报告模板
│   │   ├── scrapbook/           # 手账风
│   │   ├── classic/             # 经典暖橙
│   │   └── hack/                # 黑客风
│   └── web/                     # 前端文件
│       ├── index.html
│       ├── app.js
│       └── style.css
├── tests/                       # 单元测试
├── config/                      # 配置目录
│   └── config.json.example      # 配置模板
├── data/                        # 数据目录（运行时生成）
├── reports/                     # 报告输出目录
├── chatlog_alpha/               # chatlog 工具（需自行下载）
├── pyproject.toml               # 项目配置
├── requirements.txt             # Python 依赖
└── start.bat                    # Windows 一键启动
```

### 🧩 多平台架构

项目采用 `MessageProvider` 协议抽象数据源，核心分析引擎与平台完全解耦：

```
                    ┌─ WechatProvider (包装 ChatlogBridge)
MessageProvider ───┼─ QQProvider (未来)
  (Protocol)       └─ FileProvider (JSON 文件加载)
        │
   GroupAnalysis.providers (ProviderRegistry)
        │
   Web / MCP / CLI / Schedule (通过 registry 访问)
```

**集成新平台只需 3 步**：
1. 实现 `MessageProvider` 协议（`get_messages()`、`get_groups()` 等）
2. 在 `config.json` 中添加配置
3. 在 `main.py` 的 `_build_providers()` 中注册

详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 🙏 致谢

本项目依赖以下开源项目：

- [chatlog](https://github.com/sjzar/chatlog) — 微信聊天记录导出工具 (Apache-2.0)
- [chatlog_alpha](https://github.com/CJYKK/chatlog_backup) — chatlog 二开版本 (MIT, © lx1056758714-glitch)
- [wx_key](https://github.com/ycccccccy/wx_key) — 微信数据库密钥获取工具

## ⚠️ 免责声明

- 本工具**仅处理用户自己合法拥有的聊天数据**，或已获得数据所有者明确授权的数据。
- 严禁将本工具用于未经授权获取、查看或分析他人聊天记录，或侵犯他人隐私权。
- 用户应遵守所在国家/地区的法律法规，包括但不限于《中华人民共和国个人信息保护法》、《通用数据保护条例》(GDPR) 等。
- 使用第三方 AI 服务时，用户应仔细阅读并遵守相关服务的使用条款和隐私政策。
- **本项目按"原样"提供，不对功能的适用性、可靠性做任何保证。因使用本工具导致的任何损失，开发者概不负责。**

## 📜 更新日志

完整版本历史见 [CHANGELOG.md](CHANGELOG.md)。最近两个版本：

### v1.2.0（2026-06-11）
- ✨ 数据源页面新增日期范围选择器，SQL 层过滤避免加载全量数据
- 🐛 报告生成日期范围过滤 bug 修复（之前选一个月却生成了全部月份的报告）
- 🎨 设置页"关闭模型思考"开关与文字重叠修复
- ⚡ `get_stats()` 缓存 key 改为元组以适配日期参数

### v1.1.0（2026-06-11）
- ⚠️ AI 子分析失败原因透传（限流/超时/认证错误分类展示）
- 🔔 Web 报告预览区新增 AI 失败警告横幅
- 📊 24h 活跃度柱状图恢复 24 柱（1h 粒度）
- 📈 每日趋势图 label 自动抽稀防重叠
- 🔄 默认 AI 服务配置更新为 NVIDIA `step-3.7-flash`（128k context）
- 🔐 公开仓库化（API key 模板化，敏感信息不入仓）

## 📄 许可证

本项目基于 [MIT License](LICENSE) 开源。
