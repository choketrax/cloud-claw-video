FROM docker.io/cloudflare/sandbox:0.7.20

# 1. Install Node.js 22 (required by OpenClaw)
# The base image has Node 20, we need to replace it with Node 22
# Using direct binary download for reliability
ENV NODE_VERSION=22.22.1
RUN ARCH="$(dpkg --print-architecture)" \
    && case "${ARCH}" in \
         amd64) NODE_ARCH="x64" ;; \
         arm64) NODE_ARCH="arm64" ;; \
         *) echo "Unsupported architecture: ${ARCH}" >&2; exit 1 ;; \
       esac \
    && apt-get update && apt-get install -y xz-utils ca-certificates \
    && rm -rf /usr/local/lib/node_modules /usr/local/bin/node /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
    && curl -fsSLk https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz -o /tmp/node.tar.xz \
    && rm -rf /usr/local/lib/node_modules /usr/local/bin/node /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
    && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
    && rm /tmp/node.tar.xz \
    && node --version \
    && npm --version

# 2. Video production & audio dependencies: FFmpeg, FFprobe, python3, build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    build-essential \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# 3. Dedicated Python environment for video tools & faster-whisper
RUN python3 -m venv /opt/cloudclaw/video-venv && \
    /opt/cloudclaw/video-venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel && \
    /opt/cloudclaw/video-venv/bin/pip install --no-cache-dir \
        requests \
        librosa \
        matplotlib \
        pillow \
        numpy \
        faster-whisper

# 4. Create directories & clone immutable video skill repositories
RUN mkdir -p /opt/cloudclaw/skills /opt/cloudclaw/bin /opt/cloudclaw/cache/whisper /work/jobs && \
    git clone --depth 1 https://github.com/browser-use/video-use.git /opt/cloudclaw/skills/video-use && \
    git clone --depth 1 https://github.com/kajisho5/ffmpeg-skill.git /opt/cloudclaw/skills/ffmpeg-skill

# 5. Copy Whisper local adapter
COPY bin/whisper_transcribe.py /usr/local/bin/whisper_transcribe.py
RUN chmod +x /usr/local/bin/whisper_transcribe.py && \
    ln -s /usr/local/bin/whisper_transcribe.py /opt/cloudclaw/bin/whisper_transcribe.py

# 6. Install OpenClaw
# Pin to specific version for reproducible builds
RUN npm install -g openclaw@2026.3.23-2 \
    && openclaw --version

# Use /home/openclaw as the home directory instead of /root.
# The Sandbox SDK backup API only allows directories under /home, /workspace,
# /tmp, or /var/tmp — not /root.
ENV HOME=/home/openclaw \
    EXPLAINER_SERVICE_URL=https://explainer-test-worker.dl-56e.workers.dev \
    WHISPER_MODELS_DIR=/opt/cloudclaw/cache/whisper \
    PATH="/opt/cloudclaw/video-venv/bin:/opt/cloudclaw/bin:$PATH"

RUN mkdir -p /home/openclaw/.openclaw \
    && mkdir -p /home/openclaw/clawd \
    && mkdir -p /home/openclaw/clawd/skills \
    && ln -s /home/openclaw/.openclaw /root/.openclaw \
    && ln -s /home/openclaw/clawd /root/clawd \
    && ln -s /opt/cloudclaw/skills/video-use /home/openclaw/clawd/skills/video-use \
    && ln -s /opt/cloudclaw/skills/ffmpeg-skill /home/openclaw/clawd/skills/ffmpeg-skill

# Copy startup script
COPY start-openclaw.sh /usr/local/bin/start-openclaw.sh
RUN chmod +x /usr/local/bin/start-openclaw.sh

# Copy custom skills (anything-explainer, cloudflare-browser, CLOUDCLAW-VIDEO-PRODUCTION.md)
COPY skills/ /home/openclaw/clawd/skills/

# Ensure all files are readable for mksquashfs and writable for runtime scratch
RUN chmod -R a+rX /home/openclaw && \
    chmod -R a+rwx /work /opt/cloudclaw

# Set working directory
WORKDIR /home/openclaw/clawd

# Expose the gateway port
EXPOSE 18789
