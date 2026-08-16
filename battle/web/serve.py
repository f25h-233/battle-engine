"""battle serve — 独立服务模式（M4）：不依赖 skill/显示端也能起引擎。

默认绑定 0.0.0.0（LAN），自动生成 token——页面 meta 注入（前端自动带
X-DND-Token），POST /action 与 /roll 校验 token（401，bp 语义）。
TLS/设备审批不在本层（M4 简化面，README 注明）：LAN 内 token 已保护
POST；HTTPS 需求走显示端 --tls。
"""

from __future__ import annotations
import socket

from flask import Flask

from .bp import battle_bp


def create_app(*, token: str = "", campaign_file: str = "") -> Flask:
    """Flask 工厂：注册 battle 蓝图并注入配置（serve 与测试共用）。"""
    app = Flask(__name__)
    app.register_blueprint(battle_bp)
    app.config["BATTLE_TOKEN"] = token
    app.config["BATTLE_CAMPAIGN_FILE"] = campaign_file
    return app


def _lan_ip() -> str:
    """尽力取本机局域网 IPv4（失败回退 127.0.0.1）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))       # 仅拿路由 IP，不实际发包
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def run_server(*, host: str, port: int, token: str,
               campaign_file: str = "") -> None:
    """起服务（banner + app.run）。端口占用由调用方捕获 OSError 报错。"""
    app = create_app(token=token, campaign_file=campaign_file)
    print("── battle-engine 独立服务 ──")
    print(f"  玩家面板: http://localhost:{port}/battle/")
    if host in ("0.0.0.0", "::"):
        print(f"  局域网:   http://{_lan_ip()}:{port}/battle/")
    print(f"  POST 令牌: {token}")
    print("  结束: Ctrl+C")
    app.run(host=host, port=port, debug=False)
