# e2e 集成测试 (Sub-batch 4.4)

## 跑法

### 跑全部 e2e 测试

```powershell
# 跑全部 e2e
pytest tests/e2e/ -v

# 只跑 e2e marker
pytest tests/e2e/ -m e2e -v

# 排除 e2e (单测)
pytest tests/ -m "not e2e"
```

### 单文件

```powershell
pytest tests/e2e/test_ide_full_flow.py -v
```

## 前置条件

- 后端可以启动（`config/config.json` 存在或使用默认）
- `chatlog_alpha` 数据库**不**是必须：conftest.py 用 `mock_ga` fixture，**只** mock 掉 `ga.providers` 以避免 chatlog_alpha 真实 DB 依赖
- 不需要外部 AI API（`ai_service.api_key = ""` → 走 rule_based fallback）
- 不需要 Chrome / 浏览器（report 生成路径在 e2e 中不直接触发）

## 目录结构

```
tests/e2e/
├── __init__.py             # 标识 pytest 收集
├── conftest.py             # 5 个 fixture: e2e_tmp_dir / mock_ga / app / sync_client / async_client
├── README.md               # 本文件
├── test_ide_full_flow.py   # IDE 任务全链路（create → submit → get → completed）
├── test_api_crud.py        # REST 增删改查（health / groups / data/delete / analysis/auto / schedule/...）
└── test_report_lifecycle.py # 报告生命周期（list / download / delete / themes）
```

## Fixture 设计

| Fixture | Scope | 作用 |
|---------|-------|------|
| `e2e_tmp_dir` | session | 临时目录（reports / data 沙箱），收尾自动清理 |
| `mock_ga` | session | **真实** `GroupAnalysis` + `WebService` + `IDETaskQueue` + `AnalysisOrchestrator` + `ReportService`，**仅** mock `ga.providers` |
| `app` | session | **真实** FastAPI app（`create_app(ga=mock_ga)`） |
| `sync_client` | session | `TestClient(app, raise_server_exceptions=False)` — 让业务异常也能被测试捕到 |
| `async_client` | function | `httpx.AsyncClient(transport=ASGITransport(app))` — 给 async 测试用 |

**Path Guard 4.4.2 反向测试**：fixture 函数体内**显式**实例化真实业务类（`WebService(ga)` / `ReportService(ga)`），不是直接 `yield MagicMock()`。

## 关键路径覆盖

| 路径 | 覆盖文件 |
|------|----------|
| `/api/analysis/auto` | test_api_crud.py::test_auto_analyze_endpoint |
| `/api/ide/*` | test_ide_full_flow.py（全文件） |
| `/api/reports*` | test_report_lifecycle.py（全文件） |
| `/api/schedule/*` | test_api_crud.py::test_schedule_list_endpoint |

## strict-markers 行为

`pyproject.toml` 已开启 `--strict-markers`，未在 `markers` 列表中注册的 marker 会导致 pytest 报错。
当前已注册：`e2e` / `integration` / `slow`。

新增 e2e marker 必须先在 `pyproject.toml` 注册后再使用。

## 与单元测试的关系

e2e 测试**不**依赖 `tests/` 下其它单测，可独立运行。CI 上建议分两步：

```yaml
# 单元测试
- run: pytest tests/ -m "not e2e" -q

# e2e 集成测试（独立步骤，不加 continue-on-error: true）
- run: pytest tests/e2e/ -m e2e -v
```

## 失败排查

| 症状 | 可能原因 |
|------|----------|
| `RuntimeError: Form data requires "python-multipart"` | 缺 python-multipart 依赖（不应影响本套件） |
| `ModuleNotFoundError: chatlens.plugins.web.async_app` | pytest cwd 不在项目根（用 `cd <project_root>` 后再跑） |
| 所有测试都 `500` | 1.4 lifespan 启动失败 → 看 `chatlens.*` 日志 |
| `pytest: unknown marker: e2e` | `pyproject.toml` 未注册 `e2e` marker（已修复） |
