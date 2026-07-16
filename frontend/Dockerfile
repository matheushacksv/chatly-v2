FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ENV NUXT_PUBLIC_API_BASE=/api
RUN npm run build

FROM caddy:2-alpine
COPY --from=build /app/.output/public /srv
