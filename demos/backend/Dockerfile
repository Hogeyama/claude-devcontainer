# `./gradlew installDist` first: the context only needs build/install/backend.
FROM eclipse-temurin:17-jre-alpine
COPY build/install/backend /opt/backend
EXPOSE 8080
ENTRYPOINT ["/opt/backend/bin/backend"]
