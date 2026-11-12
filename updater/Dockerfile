# syntax=docker/dockerfile:1
# fwpipe: checks upstream, builds radio firmware and publishes it. Everything
# runs in this one container; it needs no Docker socket and no host access.
#
# The image holds the tools that do not change per build (slc-cli, its Python
# adapter pack, ZAP, a JRE, cmake/ninja/make, uv, pygbl). Arm GCC, the Silicon
# Labs SDKs and the builder's Python environment are downloaded when a build
# needs them, into /data/cache (see fwpipe/toolset.py).
#
# Build context = repository root:   docker build -f updater/Dockerfile -t fwpipe .
# x86-64 only (the toolchain table in fwpipe/toolset.py has no arm64 entries).
ARG DEBIAN_TAG=trixie-slim

FROM debian:${DEBIAN_TAG} AS silabs
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl libarchive-tools \
    && rm -rf /var/lib/apt/lists/*
# Same files and checksums as NabuCasa/silabs-firmware-builder (Silicon Labs'
# update site; the silabs.com download links need a browser).
ARG UPD=https://updates.silabs.com/studio/v6/updates/update_site/archives
RUN set -eux; cd /tmp; \
    get() { curl -fsSL -o "$1" "$3"; echo "$2  $1" | sha256sum -c -; }; \
    get python.zip 26f56b1cfa05b2b3b7dafb2c2a5e3c19498389c34dfe68be800f71f476c86363 $UPD/python/3.10.3/python.3.10.3.gtk.linux.x86_64.zip; \
    get slc.zip    f05e078b369d7e7dbfa31bdd2ad8edf61f04a9291393deab4702137639ba7d19 $UPD/slc-cli/6.0.22/slc-cli.linux.gtk.x86_64.zip; \
    get zap.zip    45537226973fb892894f7110288dd4a7db627728af8cd9fc7c27b658530e2b88 $UPD/zap/2026.06.17/zap-linux-x64.zip; \
    get jre.zip    5382fa98bcc66fc3aef48792a3e84328eca16afa9bf97517527e92812516ee50 $UPD/java21/21.0.6/linux.x86_64_21.0.6.zip; \
    mkdir -p /opt/silabs/python /opt/silabs/slc_cli /opt/silabs/zap /opt/silabs/java21; \
    bsdtar -xf python.zip --strip-components=1 -C /opt/silabs/python; \
    bsdtar -xf slc.zip --strip-components=1 -C /opt/silabs/slc_cli; \
    bsdtar -xf zap.zip -C /opt/silabs/zap; \
    bsdtar -xf jre.zip --strip-components=1 -C /opt/silabs/java21; \
    chmod +x /opt/silabs/slc_cli/slc; \
    rm -f /tmp/*.zip

FROM debian:${DEBIAN_TAG}
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git openssl libarchive-tools xz-utils \
        cmake ninja-build make \
        python3 python3-venv \
        libstdc++6 libgl1 libpng16-16 libpcre2-16-0 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/fwpipe-venv \
    && /opt/fwpipe-venv/bin/pip install --no-cache-dir "PyYAML>=6" "pygbl>=1.2.0" \
    && git config --system --add safe.directory '*' \
    && useradd --create-home --uid 1000 fwpipe
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /uvx /usr/local/bin/
COPY --from=silabs /opt/silabs /opt/silabs
COPY updater/bin/slc updater/bin/commander updater/bin/slt /opt/silabs/bin/
RUN chmod +x /opt/silabs/bin/* \
    && ln -s /opt/silabs/java21/jre/bin/java /opt/silabs/bin/java \
    && ln -s /opt/silabs/zap/zap /opt/silabs/bin/zap

# zigbee/: the panel's firmware recipe (the gsdk backend runs these scripts)
COPY zigbee/gen-ot-rcp.sh zigbee/build-ot-rcp.sh zigbee/patch-ot-rcp.py \
     zigbee/gen-ncp.sh zigbee/build-ncp.sh zigbee/patch-ncp.py \
     zigbee/gen-router.sh zigbee/build-router.sh /app/zigbee/
COPY zigbee/router/ /app/zigbee/router/
COPY updater/ /app/updater/

ENV PATH="/opt/fwpipe-venv/bin:/opt/silabs/bin:$PATH" \
    JAVA_HOME=/opt/silabs/java21/jre \
    FWPIPE_APP_ROOT=/app \
    FWPIPE_CONFIG=/app/updater/config.yaml \
    FWPIPE_STORE=/data/repos \
    FWPIPE_STATE=/data/state \
    FWPIPE_CACHE=/data/cache \
    FWPIPE_WORK=/work \
    PYTHONPATH=/app/updater \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data /work && chown fwpipe:fwpipe /data /work
USER fwpipe
ENV HOME=/home/fwpipe
VOLUME ["/data", "/work"]
WORKDIR /app/updater
ENTRYPOINT ["python3", "-m", "fwpipe"]
CMD ["loop"]
