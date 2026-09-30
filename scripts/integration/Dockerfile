FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 libportaudio2 \
    xdotool xclip x11-utils xvfb xauth dbus-x11 openbox ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv --system-site-packages /opt/venv
COPY requirements.lock /tmp/requirements.lock
RUN /opt/venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements.lock \
    && /opt/venv/bin/pip install pytest ruff
ENV PATH="/opt/venv/bin:${PATH}" PYTHONPATH=/src GSK_RENDERER=cairo PYSTRAY_BACKEND=dummy
WORKDIR /src
CMD ["dbus-run-session", "--", "xvfb-run", "-a", "-s", "-screen 0 1280x900x24", "python", "scripts/integration/recovery_desktop.py"]
