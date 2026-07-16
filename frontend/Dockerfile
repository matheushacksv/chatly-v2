FROM node:22-alpine AS build
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
ENV NUXT_PUBLIC_API_BASE=/api
RUN pnpm build

FROM caddy:2-alpine
COPY --from=build /app/.output/public /srv