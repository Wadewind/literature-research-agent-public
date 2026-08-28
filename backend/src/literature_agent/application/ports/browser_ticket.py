"""短时 Browser ticket 签发端口。"""

from typing import Protocol


class BrowserTicketIssuer(Protocol):
    def issue(self, control_id: str, revision: int) -> str: ...
