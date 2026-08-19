"""使用 Uvicorn 运行服务的入口。"""

import uvicorn

from literature_agent.main import create_app


def main() -> None:
    """运行 FastAPI 应用。"""
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
