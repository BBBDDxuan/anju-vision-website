# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend
ARG NPM_REGISTRY=https://registry.npmjs.org
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund --registry="${NPM_REGISTRY}" \
    --replace-registry-host=always --fetch-timeout=120000 --fetch-retries=2
COPY frontend/tsconfig.json frontend/vite.config.ts frontend/index.html ./
COPY frontend/src ./src
COPY frontend/public ./public
RUN npm run build

FROM python:3.13-slim-bookworm AS runtime
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CLOUD_MODE=true \
    DATA_DIR=/mnt/workspace/anju-data \
    PORT=7860 \
    SAFETY_PYTHON=/opt/detector/bin/python \
    SAFETY_DEVICE=cpu \
    FFMPEG_PATH=/usr/bin/ffmpeg \
    FFPROBE_PATH=/usr/bin/ffprobe
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# The backend and detector deliberately retain their different NumPy/OpenCV pins.
COPY requirements.txt requirements-detector.txt ./
COPY deployment/requirements-torch.txt ./deployment/requirements-torch.txt
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip check \
    && python -m venv /opt/detector \
    && /opt/detector/bin/python -m pip install --no-cache-dir \
       --index-url "${TORCH_INDEX_URL}" -r deployment/requirements-torch.txt \
    && /opt/detector/bin/python -m pip install --no-cache-dir -r requirements-detector.txt \
    && /opt/detector/bin/python -m pip check

COPY backend ./backend
COPY schemas ./schemas
COPY prompts ./prompts
COPY deployment/start_modelscope.py deployment/prepare_detector.py ./deployment/
COPY elder-safety/ai-service/config.team.json ./elder-safety/ai-service/config.team.json
COPY elder-safety/ai-service/ai_service ./elder-safety/ai-service/ai_service
COPY elder-safety/scripts/scan-safety-video.py ./elder-safety/scripts/scan-safety-video.py
COPY elder-safety/weights/manifest.json \
     elder-safety/weights/yolo11s-pose.pt \
     elder-safety/weights/indoor-dfire-yolov8n.pt ./elder-safety/weights/
COPY THIRD_PARTY_NOTICES.md ./
COPY licenses ./licenses
COPY --from=frontend /build/frontend/dist ./frontend/dist
RUN mkdir -p /mnt/workspace/anju-data \
    && python deployment/start_modelscope.py --check

EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import json,urllib.request; r=json.load(urllib.request.urlopen('http://127.0.0.1:7860/api/health', timeout=5)); assert r['status']=='ok' and r['cloud_mode']"
CMD ["python", "deployment/start_modelscope.py"]
