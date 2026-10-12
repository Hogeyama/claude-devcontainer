# `pnpm -C frontend build` first: the context only needs dist/ and nginx.conf.
FROM nginx:1.27-alpine
COPY dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
