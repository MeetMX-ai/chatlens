# Changelog

本项目的所有重要变更都记录在此文件中。格式基于 [Keep a Changelog](https://keepachangelog.com/)。

## [1.2.0] — 2026-06-11

### 新增

- **数据源页面日期范围过滤**（`/api/chatlog/load` 链路）：在数据源页面的"从 chatlog 加载"区域新增"起始日期 / 结束日期"选择器和"清除"按钮；后端 5 个文件全链路透传 `start_date` / `end_date` 参数
  - `chatlog_bridge.get_messages()` 在 SQL 层动态插入 `WHERE m.create_time >= ? AND m.create_time <= ?` 子句，过滤在数据库层完成，避免加载全量数据
  - `end_date` 自动 +86399 秒以包含整个结束日
  - 日期格式 `YYYY-MM-DD`，可选；不选 = 加载全部（向后兼容）
  - 前端做日期合法性校验（起始 > 结束给出错误提示）
- **报告生成日期范围过滤**（v1.1 修复合并）：之前选择一个月但生成了全部月份的报告，根因是 `/api/report/image/submit`、`get_stats()`、`_generate_report()` 都没有真正应用日期过滤
  - 修复后 `ReportTask` 携带 `start_date` / `end_date`，`_run_report_image_task()` / `get_stats()` / `_generate_report()` / `auto_analyze()` / `get_ai_analysis()` 全部链路透传
  - 验证：全量 1609 条 / 2026-05 一个月 1282 条 / 2026-06-04~11 一周 129 条
- **设置页面"关闭模型思考"开关修复**：之前 `<label>` 嵌套导致"关闭思考"文字和开关 UI 重叠；改为 flex 容器同一行布局，新增 `.switch-row-label` 样式，整行点击可切换（不依赖外层 label 自动绑定）

### 修复

- **设置页关闭思考 toggle 重叠**（`index.html:407-415` + `style.css:621` + `app.js:496-504`）：移除嵌套 `<label>`，把"关闭思考"和"启用模型 CoT 思考"改为同一 flex 行；点击非开关区域也能切换勾选
- **`get_stats()` 缓存 key 适配日期参数**（`analysis_orchestrator.py:107-135`）：从字符串 key 改为 `(group_name, start_date, end_date)` 元组 key，相同群不同日期范围不互相污染
- **`invalidate_cache()` 缓存清理兼容元组 key**（`analysis_orchestrator.py:137-142`）：遍历匹配 `k[0] == group_name` 时兼容 str 和 tuple

### 验证

| 测试场景 | 消息数 |
|----------|--------|
| 数据源全量 | 1761 条 |
| 数据源 2026-05 一个月 | 1282 条 |
| 数据源 2026-06-04~11 一周 | 281 条 |
| 报告生成全量 | 1609 条 |
| 报告生成 2026-05 一个月 | 1282 条 |
| 报告生成 2026-06-04~11 一周 | 129 条 |

---

## [1.1.0] — 2026-06-11

### 新增

- **AI 子分析失败原因透传**：`ai_analyzer.py` 5 个 `_do_*` 函数现在用 `__error__` 字段携带具体失败原因，orchestrator / 前端 banner 直接展示：
  - `RateLimitError (HTTP 429)` — "AI 接口限流: 请求频率过高，请稍后重试或降低并发"
  - `APITimeoutError` — "AI 接口超时: 服务端响应过慢，请检查网络或增加 timeout"
  - `AuthenticationError` — "AI 认证失败: API key 无效、过期或 base_url 配置错误"
  - `APIConnectionError` / `BadRequestError` 等 — 各自精确描述
  - API key 完全没配 — "API key 未配置"
  - 3 次重试 + Schema 修复都失败 — "第 N 次响应无法解析为 JSON（Schema 修复也失败）"
- **AI 失败警告横幅**：报告预览区上方新增 `#aiWarningsBanner`，列出每个失败 section + 真实 reason（不是"AI 未返回该字段"这种通用占位）
- **warnings 旁路文件**：每次生成报告时把 `task.warnings` 写入 `<html_path>.warnings.json` 侧车文件，报告历史列表也能读到
- **配置模板文件**：`config/config.example.json` 和 `chatlens/config/config.example.json`（含 `YOUR_NVAPI_KEY_HERE` 占位符），方便 clone 后快速配置
- **公开仓库**：项目已推送到 https://github.com/MeetMX-ai/chatlens，126 个文件，MIT 协议

### 变更

- **24 小时活跃度柱状图恢复 24 柱**（`image_report.py:226-235`）：之前按 3 小时一档聚合，丢失粒度；现在 1 小时一档，labels 显示 `21` / `00` 等具体小时
- **每日消息趋势 X 轴 label 抽稀**（`svg_charts.py:72-90`）：
  - ≤ 14 个数据点：全部画 label
  - > 14 个数据点：等距抽 12 个 label，**首尾必画**
  - 解决长周期（日级别）图表 label 互相挤压重叠的问题
- **AI 服务默认配置更新**（`config/config.json`）：
  - `model`: `deepseek-ai/deepseek-v4-flash` → **`stepfun-ai/step-3.7-flash`**（NVIDIA 平台，更适合长上下文）
  - `max_tokens`: `16384` → **`128000`** (128k)
  - `temperature`: `1` → **`0.7`**
  - `ai_timeout`: 60s → **`180s`** (3 分钟，适配 128k 输出)
  - `enable_thinking`: `false`（保持关闭，节省 token）
  - `concurrent_workers`: `1`（保持串行，避免限流）
- **`.gitignore` 强化**：
  - 忽略 `node_modules/` / `dist/` / `*.tsbuildinfo`（TypeScript 子项目产物）
  - 忽略 `*.err` / `*.out` / `*.log` / `chatlens_8080.*` / `chatlog.*` / `logs_web.*` 等运行时日志
  - 忽略 `tmp_*.py` / `verify_*.py` / `quick_check.py` / `find_hang.py` / `dummy_*.py` / `g4_*.py` / `sse_*.py` / `restart-chrome-with-cdp.*` 等调试脚本
  - 忽略 `chatlens/config/config.json`（与 `config/config.json` 同步管理，避免密钥泄露）
  - 忽略 `run_verify.py` / `test_run_x.txt` 根目录遗留文件

### 修复

- **AI 子分析失败时不再静默**：之前 AI 返回空数据时 rules 引擎自动补齐，导致报告显示"正常"但实际 AI 失败；现在**显式报错**，用户能看到具体哪个 section 失败、为什么失败
- **前端 `showHtmlPreview` 4 个调用点统一传 warnings**：之前 3/4 的调用点漏传 `result.warnings`，导致 banner 在主路径（AI 分析页 SSE 成功回调、IDE 任务成功、报告历史点击）下不显示
- **orchestrator HTML-only 路径 `task.result` 补 `"warnings"` 字段**：之前两步流程分支（先看 HTML 再决定是否出图）的 result 不带 warnings
- **AI 接口限流时不再雪崩**：串行调用（`concurrent_workers: 1`）+ 退避重试，避免 5 个子分析并发打满 429 配额

### 安全

- **API key 不再入仓**：`config/config.json` 和 `chatlens/config/config.json` 都在 `.gitignore` 中，公开仓库不含任何真 NVIDIA key
- **公开仓库扫描验证**：推送前对 126 个待入仓文件做 regex 扫描（`nvapi-` / `sk-` / `ghp_`），命中 0 个真密钥

### 架构

- `_classify_error(e)` helper 统一分类 OpenAI 异常，避免在 5 个 `_do_*` 函数里重复 except
- `_make_ai_postprocess(id_map)` 工厂统一 postprocess 模板，`__error__` 透传逻辑只写一次
- 4 层兜底链路：API 整体异常 → 第一层（orchestrator 兜底回 rules）→ 第二层（image_report 渲染兜底）→ 第三层（模板 `{% if %}`）。本次 v1.1.0 拆掉了中间两层（用户偏好显式报错），但保留了模板层作为最后一道防线
- 4 处 `showHtmlPreview(url, file, warnings)` 调用点统一 warnings 传递

---

## [1.0.0] — 2026-06-02

### 新增

- **三端接入**：Web 界面、CLI 命令行（`wxcli`）、MCP 服务器（支持 Trae/Claude Code/Cursor）
- **数据解密**：自动调用 chatlog 解密微信本地数据库
- **统计分析**：消息概览、成员排名、时间分布、互动分析、话题聚类
- **AI 智能分析**：用户称号/MBTI画像、金句识别、聊天质量锐评（API Key / Ollama / 规则分析三级降级）
- **报告生成**：HTML / JPG / PNG / PDF 多格式输出，3 套主题模板（手账风 / 经典暖橙 / 黑客风）
- **定时任务**：持久化调度器，支持定时解密分析与报告生成
- **多平台架构**：`MessageProvider` 协议抽象数据源，核心引擎与平台完全解耦
- **插件化设计**：`PluginRegistry` 自动发现与加载，新增功能零侵入
- **线程安全**：`ChatlogBridge` 数据库锁、`IDETaskQueue` 并发保护、`_timeout_watcher` Event 机制
- **配置管理**：`config.json.example` 模板、`.gitignore` 排除敏感文件

### 架构

- `core/` 层：分析引擎、AI 分析、数据模型（平台无关）
- `plugins/` 层：Web / CLI / MCP / Report / Schedule 五大插件
- `providers.py`：`MessageProvider` Protocol + `ProviderRegistry` + `WechatProvider`
- `GroupAnalysis.chatlog` 向后兼容属性，平滑迁移

### 技术栈

- Python ≥ 3.10
- MCP 协议 1.26.0（IDE 集成）
- Jinja2 3.1.6（模板渲染）
- Pillow 12.1.1（图像处理）
- PyMuPDF 1.27.2.3（PDF 生成）
- OpenAI 兼容 API（AI 分析）
- jieba 中文分词
