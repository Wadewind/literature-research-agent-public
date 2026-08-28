# 本地 OpenSandbox Server

本文只用于 Phase 6 本地单人演示。OpenSandbox Server 不进入普通 Compose，不读取用户 home 配置，也不
构成公网部署或生产隔离方案。

## 固定版本与边界

- Worker SDK：`opensandbox==0.1.15`；
- Server：`opensandbox-server==0.2.2`；
- 配置：`config/opensandbox-server.phase6.toml`；
- 启动入口：`scripts/opensandbox-server.sh`；
- research image：`agent-service/research-agent-sandbox@sha256:8ded4a3cfb5603efac3e297a09f79f4bdef798379728eeb96d563ae8f99f40d1`；
- execd image：`opensandbox/execd@sha256:1dc98c7de10b9a73450ac75aa0f200ad7972f2c40f5225f6a8998e166b45d6dd`；
- egress image：`opensandbox/egress@sha256:973130e01bf76e8e686e2853ebf47b21741bc8781919bb4a7cf60af09a3c6e8a`。

本机 Docker inspect 已确认上述三个 digest 存在并与镜像 ID 匹配；重建 research image 后必须显式审查并
更新 digest，不能静默退回 tag。Server 仅监听 `127.0.0.1:8080`，使用 Docker bridge、空 host path
allowlist、drop capability ALL、no-new-privileges、PID 256、固定端口范围和 default-deny egress。

## 固定镜像准备与 fail-closed 核验

从当前仓库构建 research image 候选版本。构建使用 Dockerfile 已固定的 base image、npm lock 和 pip hash
lock，但 apt 仓库内容与构建器版本仍可能改变结果，所以“构建成功”不等于得到已审核 digest：

```bash
docker build \
  --tag agent-service/research-agent-sandbox:phase6-8.5 \
  sandbox/research-agent
```

当前本地审核 pin 是在上一版已审核 digest 上只叠加 `start-vnc-server` 的最小迁移结果；可用
`Dockerfile.vnc-overlay` 精确复核本次 VNC 差异。完整 Dockerfile 同步包含相同 wrapper，供后续从固定
upstream stage 重建：

```bash
DOCKER_BUILDKIT=0 docker build \
  --file sandbox/research-agent/Dockerfile.vnc-overlay \
  --tag agent-service/research-agent-sandbox:phase6-8.5 \
  sandbox/research-agent
```

wrapper 使 base entrypoint 启动的 `Xtigervnc` 只监听 Sandbox namespace loopback，并固定
`SecurityTypes=None`。身份校验仍发生在平台 Browser ticket gateway；5901 不映射到宿主或跨 Sandbox，
VNC 密码也不会进入前端。模型在同一 Sandbox 内理论上可访问该 loopback 端口，但它已拥有同一 Chromium
的固定 Playwright MCP/CDP 与 `execute`，因此没有扩大到宿主或其他 Session；这仍只属于本地单人演示边界。

execd 与 egress 可以按审核过的 digest 获取；若 registry 返回的内容不匹配，Docker 必须拒绝 pull：

```bash
docker pull opensandbox/execd@sha256:1dc98c7de10b9a73450ac75aa0f200ad7972f2c40f5225f6a8998e166b45d6dd
docker pull opensandbox/egress@sha256:973130e01bf76e8e686e2853ebf47b21741bc8781919bb4a7cf60af09a3c6e8a
```

启动前必须核对候选 research tag、三个固定引用的 Image ID/RepoDigest，以及 Server TOML 中的
execd/egress pin。任一 `inspect`、ID 比较或配置匹配失败都停止启动，不改用 `latest` 或普通 tag：

```bash
phase6_research_tag='agent-service/research-agent-sandbox:phase6-8.5'
phase6_research_pin='agent-service/research-agent-sandbox@sha256:8ded4a3cfb5603efac3e297a09f79f4bdef798379728eeb96d563ae8f99f40d1'
phase6_execd_pin='opensandbox/execd@sha256:1dc98c7de10b9a73450ac75aa0f200ad7972f2c40f5225f6a8998e166b45d6dd'
phase6_egress_pin='opensandbox/egress@sha256:973130e01bf76e8e686e2853ebf47b21741bc8781919bb4a7cf60af09a3c6e8a'

test "$(docker image inspect --format '{{.Id}}' "$phase6_research_tag")" = \
  "$(docker image inspect --format '{{.Id}}' "$phase6_research_pin")"

docker image inspect \
  --format 'ID={{.Id}} RepoDigests={{json .RepoDigests}}' \
  "$phase6_research_pin" "$phase6_execd_pin" "$phase6_egress_pin"

rg -F "$phase6_execd_pin" config/opensandbox-server.phase6.toml
rg -F "$phase6_egress_pin" config/opensandbox-server.phase6.toml
rg -F "$phase6_research_pin" .env.example
```

本地重建若得到不同 Image ID，表示候选内容与当前审核镜像不同；不得给新镜像沿用旧 digest。应先审查差异、
重跑显式 Smoke，再通过独立变更同步 Provider 配置、`.env.example`、本运行手册与相关契约。当前 research
image 是本地演示资产，没有在本阶段提供可跨机器获取的生产镜像仓库；新机器无法解析固定引用时必须停止，
不能用重新构建的近似镜像冒充审核结果。以上核验只证明本地镜像引用一致，不是生产 Sandbox 安全结论。

## 启动

先安装固定 Server，并准备固定镜像。不要把 API key 写入仓库或命令历史；以下使用隐藏输入：

```bash
uv tool install 'opensandbox-server==0.2.2'
read -rsp 'OpenSandbox local API key: ' OPENSANDBOX_SERVER_API_KEY
echo
export OPENSANDBOX_SERVER_API_KEY
./scripts/opensandbox-server.sh
```

另一个终端启动 `--real` Worker/API 时，把同一值作为
`AGENT_RESEARCH_SANDBOX_API_KEY`，并保持 domain/protocol/image 与 `.env.example` 一致。脚本会拒绝错误
Server 版本和空 API key；不会启用 insecure bypass。

## 显式安全 Smoke

普通测试保持离线。只有本地 Server 已启动且固定镜像可用时，才运行：

```bash
cd backend
AGENT_RUN_OPENSANDBOX_SECURITY_TESTS=1 \
AGENT_RESEARCH_SANDBOX_API_KEY="$OPENSANDBOX_SERVER_API_KEY" \
.venv/bin/pytest -q tests/infrastructure/test_opensandbox_security_smoke.py -rA
```

Smoke 创建可删除 Sandbox，验证非 root、CPU/内存/PID、无平台 Secret/宿主挂载、命令进程组超时后仍可
继续执行、输出边界、60 秒 TTL、重复销毁，以及 Bash、Python、Node、Chromium、Playwright 与固定 Search
MCP 的统一 default-deny。
它不调用模型，也不会成功访问 arXiv。结束后检查没有残留 Sandbox/egress 容器，再停止 Server。

Phase 6 的 Browser 与 public-egress 证据使用独立显式开关：

```bash
cd backend
AGENT_RUN_OPENSANDBOX_BROWSER_TESTS=1 \
AGENT_RESEARCH_SANDBOX_API_KEY="$OPENSANDBOX_SERVER_API_KEY" \
.venv/bin/pytest -q \
  tests/infrastructure/test_opensandbox_browser_control_smoke.py::test_real_opensandbox_vnc_and_playwright_share_one_sandbox

AGENT_RUN_OPENSANDBOX_PUBLIC_EGRESS_TESTS=1 \
AGENT_RESEARCH_SANDBOX_API_KEY="$OPENSANDBOX_SERVER_API_KEY" \
.venv/bin/pytest -q tests/infrastructure/test_opensandbox_public_egress_smoke.py
```

Browser Smoke 装配生产 noVNC 组件、ticket relay 与同一 Sandbox Playwright MCP；public-egress Smoke 会
访问固定 arXiv/example.com 目标并验证 private/metadata/宿主目标拒绝。两者都不调用模型，但后者会访问
实时公网；失败时不得把旧结果或离线测试冒充当前真实证据。

## 已知限制

Server 0.2.2 的本地 Docker resource 请求只实际解析 CPU、memory 和 GPU；没有请求级 overlay 物理磁盘硬
配额。WorkspaceSnapshot 的 128 文件、单文件 10 MiB、总 50 MiB 和 Artifact 上限是业务提交边界，不是
物理磁盘隔离。当前未配置 secure runtime、公开 ingress、镜像仓库、公网多租户认证或生产 SLA。
