# Multi-stage build (ADR-014/061): Node build, Node runtime.
#
# The Vue app was static files behind nginx. The console server-renders and
# proxies /api itself, so the runtime is Node — nginx is no longer in the path.
FROM node:22-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .

# Next serialises rewrites into the build output, so the API target has to be
# known here rather than at container start.
ARG API_ORIGIN=http://backend:8000
ENV API_ORIGIN=${API_ORIGIN}
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# `output: standalone` emits a server plus exactly the dependencies it loads.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

USER node
EXPOSE 3000
CMD ["node", "server.js"]
