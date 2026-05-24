FROM node:22-alpine AS build

WORKDIR /app
COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM node:22-alpine AS runtime

ENV NODE_ENV=production
ENV PORT=3000
ENV HOST=0.0.0.0
ENV DATA_DIR=/data

WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev && npm cache clean --force

COPY --from=build /app/dist ./dist
RUN mkdir -p /data && chown -R node:node /app /data

USER node
EXPOSE 3000
VOLUME ["/data"]

CMD ["node", "dist/server/index.js"]
