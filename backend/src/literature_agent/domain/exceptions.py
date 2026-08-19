"""领域异常。"""


class ProjectNotFoundError(Exception):
    """请求的资源不存在或当前 actor 无权访问。"""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(f"Project {project_id} 不存在")
