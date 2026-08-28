"""不落库的 HMAC Browser ticket。"""

import base64
import hashlib
import hmac


class HmacBrowserTicketIssuer:
    """从稳定 control identity 确定性签发短时 opaque ticket。"""

    def __init__(self, secret: bytes) -> None:
        if len(secret) < 32:
            raise ValueError("Browser ticket secret 至少需要 32 bytes")
        self._secret = secret

    def issue(self, control_id: str, revision: int) -> str:
        if not control_id or revision < 1:
            raise ValueError("Browser control identity 非法")
        digest = hmac.new(
            self._secret,
            f"browser-control.v1:{control_id}:{revision}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
