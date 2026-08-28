"""使用系统 DNS 的有界声明来源目标解析 Adapter。"""

import asyncio
import socket


class SystemPublicSourceResolver:
    def __init__(self, *, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("DNS 解析超时必须为正数")
        self._timeout_seconds = timeout_seconds

    async def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
        loop = asyncio.get_running_loop()
        results = await asyncio.wait_for(
            loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM),
            timeout=self._timeout_seconds,
        )
        return tuple(sorted({str(item[4][0]) for item in results}))
