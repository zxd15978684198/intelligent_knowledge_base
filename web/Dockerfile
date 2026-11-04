# PyKnora Web（Next 16 standalone）多阶段镜像。
#
# 为什么是 standalone：Next 默认产物要带 node_modules 才能 `next start`，
# 镜像动辄 500MB+；standalone 只带最小运行时，配合 `.next/static` 与 public 即可跑。
# 代价：rewrites() 在构建期求值，`API_PROXY_TARGET` 必须是 build arg（见下）。
#
# 构建（在 web/ 目录，或由 docker-compose.yml 指定 context: ./web）：
#   docker build -t pyknora-web:local --build-arg API_PROXY_TARGET=http://api:8010 .
# syntax=docker/dockerfile:1.7
ARG NODE_VERSION=22

# ---------------------------------------------------------------- deps
FROM node:${NODE_VERSION}-alpine AS deps

RUN corepack enable

WORKDIR /app

# 只拷锁文件：依赖不变就不会重装
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

# ---------------------------------------------------------------- builder
FROM node:${NODE_VERSION}-alpine AS builder

RUN corepack enable

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# 容器里的同源代理必须指向 compose 网络里的服务名，不能是 127.0.0.1：
# 浏览器访问的是 web 容器，`/api/*` 由 web 容器自己转发，回环地址在它内部不通。
ARG API_PROXY_TARGET=http://api:8010
ENV API_PROXY_TARGET=${API_PROXY_TARGET} \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production

RUN pnpm build

# ---------------------------------------------------------------- runtime
FROM node:${NODE_VERSION}-alpine AS runtime

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup --gid 10001 nodejs \
    && adduser --uid 10001 --ingroup nodejs --system --shell /sbin/nologin nextjs

WORKDIR /app

# standalone 产物自带 server.js 与精简 node_modules
COPY --from=builder --chown=10001:10001 /app/.next/standalone ./
COPY --from=builder --chown=10001:10001 /app/.next/static ./.next/static
# 本仓库没有 public/（图标全部内联/由后端提供），有的话放开下一行：
# COPY --from=builder --chown=10001:10001 /app/public ./public

USER 10001:10001

EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD node -e "fetch('http://127.0.0.1:3000/login').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
