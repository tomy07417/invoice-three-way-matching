FROM node:24-bookworm-slim
RUN npm install --global pnpm@10.28.2
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY frontend/package.json ./frontend/package.json
RUN pnpm install --frozen-lockfile
COPY frontend/tsconfig.json frontend/next-env.d.ts ./frontend/
COPY frontend/app ./frontend/app
RUN pnpm --filter frontend build && chown -R node:node /app
USER node
EXPOSE 3000
CMD ["pnpm", "--filter", "frontend", "start"]
