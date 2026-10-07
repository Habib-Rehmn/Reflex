import asyncio
from pathlib import Path

from app.config import Settings
from app.gateway import Gateway
from app.main import create_app


def test_dashboard_route_and_static_mount(tmp_path):
    app = create_app(
        Gateway(),
        settings=Settings(
            _env_file=None, system2_provider="disabled", database_path=str(tmp_path / "test.db")
        ),
    )
    route = next(route for route in app.routes if route.path == "/")
    response = asyncio.run(route.endpoint())
    assert Path(response.path).is_file()
    assert response.media_type == "text/html"
    mount = next(route for route in app.routes if route.path == "/static")
    assert Path(mount.app.directory) == Path(response.path).parent


def test_dashboard_assets_exist_and_use_text_rendering():
    root = Path("app/static")
    html = (root / "index.html").read_text()
    js = (root / "dashboard.js").read_text()
    assert "/static/dashboard.css" in html
    assert "/static/dashboard.js" in html
    assert 'aria-live="polite"' in html
    assert 'label for="prompt"' in html
    assert "innerHTML" not in js
    assert "textContent = body.answer" in js
    assert 'request("/metrics")' in js
    assert 'request("/requests?limit=20")' in js
