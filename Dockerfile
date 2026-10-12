FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && useradd --uid 1000 --no-create-home leva \
 && mkdir /data && chown leva /data
COPY leva2mqtt ./leva2mqtt

USER leva
VOLUME /data
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD ["python", "-m", "leva2mqtt", "--healthcheck"]
ENTRYPOINT ["python", "-m", "leva2mqtt"]
