FROM node:20-alpine AS build
WORKDIR /app
# The lockfile is committed, so the image build resolves exactly the tree that CI
# tested rather than whatever is newest at build time.
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
