# Linux build + test image for DDP Streamer. Ubuntu 22.04 (glibc 2.35): the build
# runs on that and newer. Python 3.14 from the deadsnakes PPA (22.04 has 3.10, and
# PyAV 19 needs 3.12+), so all systems ship the same Python, PyAV and FFmpeg.
# From the repo root:
#   docker build -f linux/Dockerfile -t ddp-build .
#   docker run --rm -u $(id -u):$(id -g) -e HOME=/tmp -v $PWD:/src ddp-build python build.py
FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends software-properties-common gpg-agent \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
      python3.14 python3.14-venv python3.14-dev python3.14-tk binutils \
      libportaudio2 libgl1 libglib2.0-0 fonts-dejavu-core \
      libxcb-render0 libxcb-randr0 libxcb-shm0 libxcb-xfixes0 \
      xvfb x11-utils imagemagick xdotool ca-certificates curl file desktop-file-utils \
    && rm -rf /var/lib/apt/lists/*
# AppImage tool (unpacked: Docker has no FUSE) and the runtime it puts in front of
# the app. That runtime is static, so users don't need libfuse2.
RUN curl -fsSL -o /tmp/appimagetool https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage \
    && chmod +x /tmp/appimagetool && cd /opt && /tmp/appimagetool --appimage-extract >/dev/null \
    && mv squashfs-root appimagetool && chmod -R a+rX appimagetool \
    && ln -s /opt/appimagetool/AppRun /usr/local/bin/appimagetool \
    && rm /tmp/appimagetool \
    && curl -fsSL -o /opt/runtime-x86_64 https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64
ENV APPIMAGE_RUNTIME=/opt/runtime-x86_64
RUN python3.14 -m venv /venv
ENV PATH=/venv/bin:$PATH
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -U pip && pip install --no-cache-dir -r /tmp/requirements.txt pyinstaller
WORKDIR /src
