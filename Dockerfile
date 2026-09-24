FROM ubuntu:24.04

ARG ATLAS_COMMIT=044073c9dca5aa6c1554aa9217631ec564c9ef3c
ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/root/.local/bin:${PATH}

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl docker.io git python3 python3-pip python3-venv tini \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m pip install --break-system-packages \
      "git+https://github.com/swebenchatlasanon-code/swe-bench-atlas-anon@${ATLAS_COMMIT}"

COPY patch_atlas_java.py /tmp/patch_atlas_java.py
RUN python3 /tmp/patch_atlas_java.py && rm /tmp/patch_atlas_java.py

WORKDIR /bench
COPY bench.py /usr/local/bin/atlas-bench
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/atlas-bench /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]
