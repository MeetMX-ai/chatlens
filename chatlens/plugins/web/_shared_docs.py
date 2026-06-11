from typing import List, Dict, Any

REPORT_API_DOCS: List[Dict[str, Any]] = [
    {
        "path": "/api/report/themes",
        "method": "GET",
        "description": "列出可用报告主题",
        "parameters": [],
    },
    {
        "path": "/api/report/image",
        "method": "GET",
        "description": "生成群聊报告图片",
        "parameters": [
            {"name": "group", "type": "string", "required": True},
            {"name": "theme", "type": "string", "required": False},
            {"name": "fmt", "type": "string", "required": False},
            {"name": "skip_ai", "type": "bool", "required": False},
        ],
    },
    {
        "path": "/api/report/pdf",
        "method": "GET",
        "description": "生成群聊报告 PDF",
        "parameters": [
            {"name": "group", "type": "string", "required": True},
            {"name": "theme", "type": "string", "required": False},
            {"name": "skip_ai", "type": "bool", "required": False},
        ],
    },
    {
        "path": "/api/report.html",
        "method": "GET",
        "description": "生成群聊报告 HTML 数据",
        "parameters": [
            {"name": "group", "type": "string", "required": True},
        ],
    },
    {
        "path": "/api/reports",
        "method": "GET",
        "description": "列出已生成的报告文件",
        "parameters": [],
    },
    {
        "path": "/api/reports/delete",
        "method": "DELETE",
        "description": "删除报告文件",
        "parameters": [
            {"name": "filename", "type": "string", "required": True},
        ],
    },
    {
        "path": "/api/reports/download",
        "method": "GET",
        "description": "下载报告文件",
        "parameters": [
            {"name": "file", "type": "string", "required": True},
        ],
    },
    {
        "path": "/api/analysis/generate-image",
        "method": "POST",
        "description": "从 HTML 文件生成图片",
        "parameters": [
            {"name": "html_file", "type": "string", "required": True},
            {"name": "fmt", "type": "string", "required": False},
        ],
    },
    {
        "path": "/api/analysis/daily/auto-report",
        "method": "POST",
        "description": "生成指定日期的报告",
        "parameters": [
            {"name": "group_name", "type": "string", "required": True},
            {"name": "date", "type": "string", "required": True},
        ],
    },
    {
        "path": "/api/analysis/ide-prompt",
        "method": "POST",
        "description": "获取 IDE 分析 prompt",
        "parameters": [
            {"name": "group_name", "type": "string", "required": True},
        ],
    },
    {
        "path": "/api/ide/task/create",
        "method": "POST",
        "description": "创建 IDE 任务",
        "parameters": [
            {"name": "group_name", "type": "string", "required": True},
        ],
    },
    {
        "path": "/api/ide/task/result",
        "method": "POST",
        "description": "提交 IDE 任务结果",
        "parameters": [
            {"name": "task_id", "type": "string", "required": True},
            {"name": "result", "type": "object", "required": True},
        ],
    },
    {
        "path": "/api/ide/task",
        "method": "GET",
        "description": "查询 IDE 任务状态",
        "parameters": [
            {"name": "task_id", "type": "string", "required": True},
        ],
    },
    {
        "path": "/api/ide/tasks/pending",
        "method": "GET",
        "description": "列出待处理的 IDE 任务",
        "parameters": [],
    },
    {
        "path": "/api/analysis/ai",
        "method": "POST",
        "description": "AI 分析群聊",
        "parameters": [
            {"name": "group_name", "type": "string", "required": True},
        ],
    },
    {
        "path": "/api/analysis/daily-dates",
        "method": "GET",
        "description": "获取有消息的日期列表",
        "parameters": [
            {"name": "group", "type": "string", "required": True},
        ],
    },
    {
        "path": "/api/analysis/daily",
        "method": "GET",
        "description": "获取指定日期的统计",
        "parameters": [
            {"name": "group", "type": "string", "required": True},
            {"name": "date", "type": "string", "required": True},
        ],
    },
    {
        "path": "/api/chatlog/load",
        "method": "POST",
        "description": "从 chatlog 加载消息",
        "parameters": [
            {"name": "talker", "type": "string", "required": True},
            {"name": "limit", "type": "integer", "required": False},
        ],
    },
    {
        "path": "/api/chatlog/refresh",
        "method": "GET",
        "description": "刷新 chatlog 数据库",
        "parameters": [],
    },
    {
        "path": "/api/chatlog/chatrooms",
        "method": "GET",
        "description": "列出所有群聊",
        "parameters": [],
    },
    {
        "path": "/api/chatlog/talkers",
        "method": "GET",
        "description": "列出所有聊天对象",
        "parameters": [],
    },
    {
        "path": "/api/config",
        "method": "GET",
        "description": "获取当前配置",
        "parameters": [],
    },
    {
        "path": "/api/config/save",
        "method": "POST",
        "description": "保存配置",
        "parameters": [
            {"name": "ai_service", "type": "object", "required": False},
        ],
    },
    {
        "path": "/api/data-files",
        "method": "GET",
        "description": "列出已加载的数据文件",
        "parameters": [],
    },
    {
        "path": "/api/schedule/create",
        "method": "POST",
        "description": "创建定时任务",
        "parameters": [
            {"name": "group_name", "type": "string", "required": True},
            {"name": "hour", "type": "integer", "required": True},
            {"name": "minute", "type": "integer", "required": True},
        ],
    },
    {
        "path": "/api/schedule/list",
        "method": "GET",
        "description": "列出定时任务",
        "parameters": [],
    },
    {
        "path": "/api/schedule/toggle",
        "method": "POST",
        "description": "启用/禁用定时任务",
        "parameters": [
            {"name": "task_id", "type": "string", "required": True},
            {"name": "enabled", "type": "bool", "required": True},
        ],
    },
    {
        "path": "/api/schedule/trigger",
        "method": "POST",
        "description": "手动触发定时任务",
        "parameters": [
            {"name": "task_id", "type": "string", "required": True},
        ],
    },
    {
        "path": "/api/schedule/delete",
        "method": "DELETE",
        "description": "删除定时任务",
        "parameters": [
            {"name": "task_id", "type": "string", "required": True},
        ],
    },
]


def get_report_api_docs() -> List[Dict[str, Any]]:
    return REPORT_API_DOCS
