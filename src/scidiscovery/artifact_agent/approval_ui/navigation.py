"""Shared navigation for human-facing pages; links never change bindings."""
from html import escape
from urllib.parse import quote


def navigation(instance_id: str | None = None) -> str:
    current = ("<a href='/instance/" + quote(instance_id, safe="") + "'>当前实例</a>"
               + "<a href='/instance/" + quote(instance_id, safe="") + "/results'>科学结果 / Best</a>"
               + "<a href='/instance/" + quote(instance_id, safe="") + "/settings'>Agent 设置</a>"
               if instance_id else "")
    return ("<nav class='global-navigation' aria-label='全局导航'>"
            "<a class='home-button' href='/'>返回首页</a>"
            "<a href='/instances/manage'>实例管理</a>" + current + "</nav>")


def hidden(name: str, value: str) -> str:
    return f"<input type='hidden' name='{escape(name, quote=True)}' value='{escape(value, quote=True)}'>"
