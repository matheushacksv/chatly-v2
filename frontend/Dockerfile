FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ENV NUXT_PUBLIC_API_BASE=/api
# o generate baixa as fontes do Google; o happy eyeballs do Node 24 dá 250ms por tentativa e,
# a partir das VPS (chatly e seabra), a conexão com fonts.gstatic.com passa disso: ETIMEDOUT em
# ~340ms e build quebrado. Sem a autosseleção de família a conexão sai normal.
ENV NODE_OPTIONS=--no-network-family-autoselection
RUN npx nuxt generate

FROM caddy:2-alpine
COPY --from=build /app/.output/public /srv
