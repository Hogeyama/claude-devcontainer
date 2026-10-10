FROM busybox:1.37
COPY marker /marker
RUN test "$(cat /marker)" = offline-build-ok
CMD ["cat", "/marker"]
