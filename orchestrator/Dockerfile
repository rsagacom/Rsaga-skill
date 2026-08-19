FROM node:22-alpine

WORKDIR /app
RUN addgroup --system --gid 10001 studio \
    && adduser --system --uid 10001 --ingroup studio studio
COPY package.json package-lock.json tsconfig.json ./
RUN npm ci
COPY src ./src
RUN chown -R studio:studio /app

USER studio:studio

EXPOSE 8790

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=6 \
  CMD node -e "fetch('http://127.0.0.1:8790/health').then(r => { if (!r.ok) process.exit(1) }).catch(() => process.exit(1))"

CMD ["npm", "run", "server"]
