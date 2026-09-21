FROM node:20-slim AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

FROM node:20-slim AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# PDF.js worker is served from /pdf.worker.min.mjs (not committed).
RUN cp node_modules/pdfjs-dist/build/pdf.worker.min.mjs public/pdf.worker.min.mjs
# Rewrites in next.config.ts read BACKEND_URL at build time. Compose runtime
# env is not visible here, so default to the in-network backend hostname.
ARG BACKEND_URL=http://loma-backend:3000
ENV BACKEND_URL=$BACKEND_URL
# Which login methods the UI shows. NEXT_PUBLIC_* is inlined at build time, so it
# must be a build arg (default "local"); set to "google" or "local,google" to show
# the Google button. Passed via docker-compose build.args from the project .env.
ARG NEXT_PUBLIC_AUTH_PROVIDER=local
ENV NEXT_PUBLIC_AUTH_PROVIDER=$NEXT_PUBLIC_AUTH_PROVIDER
RUN npm run build

FROM node:20-slim
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app ./
EXPOSE 3001
CMD ["npm", "run", "start"]
