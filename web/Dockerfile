# Construit depuis le dossier web/ :
#   docker compose -f web/docker-compose.yml build
# nginx sans root, à l'écoute sur le port 8080
FROM nginxinc/nginx-unprivileged:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY static/ /usr/share/nginx/html/app/

EXPOSE 8080
