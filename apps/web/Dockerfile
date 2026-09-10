FROM node:20-alpine AS base

ENV PNPM_HOME=/pnpm \
    PATH="/pnpm:$PATH" \
    NEXT_TELEMETRY_DISABLED=1

RUN corepack enable
WORKDIR /app

FROM base AS deps

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json ./apps/web/
COPY packages ./packages
RUN pnpm install --frozen-lockfile

FROM base AS development

COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /app/apps/web/node_modules ./apps/web/node_modules
COPY . .

WORKDIR /app/apps/web
EXPOSE 3000

CMD ["pnpm", "dev"]

FROM base AS production-builder

ARG API_PROXY_TARGET=http://api:8000

ENV API_PROXY_TARGET=$API_PROXY_TARGET \
    NEXT_PUBLIC_API_URL=""

COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /app/apps/web/node_modules ./apps/web/node_modules
COPY . .

RUN pnpm --filter web build

FROM node:20-alpine AS production

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000

WORKDIR /app

RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 --ingroup nodejs nextjs

COPY --from=production-builder --chown=nextjs:nodejs /app/apps/web/public ./apps/web/public
COPY --from=production-builder --chown=nextjs:nodejs /app/apps/web/.next/standalone ./
COPY --from=production-builder --chown=nextjs:nodejs /app/apps/web/.next/static ./apps/web/.next/static

USER nextjs

EXPOSE 3000

CMD ["node", "apps/web/server.js"]
