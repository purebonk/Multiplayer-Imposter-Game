# Matches the Python version pinned for the existing Render deploy
# (PYTHON_VERSION=3.12.12), so the container and the non-Docker path build on
# the same interpreter. "slim" keeps the image small by omitting the build
# toolchain and docs that the full image carries; nothing here needs to
# compile C extensions, since every pinned dependency ships wheels.
FROM python:3.12.12-slim

# Faster, quieter, and no stray .pyc files baked into the image.
#   PYTHONDONTWRITEBYTECODE - don't write .pyc (the layer is read-only anyway)
#   PYTHONUNBUFFERED        - stream logs straight out so `docker logs` and
#                             Render's log viewer show them in real time
#                             instead of holding them in a buffer
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy ONLY the dependency manifest first, then install. Docker caches each
# layer and invalidates it when its inputs change: keeping this above the
# application code means editing game.py or app.js reuses the cached install
# layer, and pip only reruns when requirements.txt itself changes. Doing
# `COPY . .` before installing would throw that cache away on every code edit.
COPY requirements.txt .

# --no-cache-dir: pip's download cache is useless in an image and just adds
# weight, since the layer is never reused for another install.
RUN pip install --no-cache-dir -r requirements.txt

# Now the application code -- the layer that actually changes often.
COPY . .

# Documentation only; the real port comes from $PORT at runtime.
EXPOSE 8000

# Shell form (not the JSON exec form) on purpose: the exec form does NOT run a
# shell, so "$PORT" would be passed to uvicorn as a literal string rather than
# expanded. Render injects PORT into the container the same way it does for
# the non-Docker deploy; the default keeps `docker run` working locally where
# PORT is unset. --host 0.0.0.0 is required so the platform's proxy can reach
# the process -- uvicorn's 127.0.0.1 default would only accept connections
# from inside the container.
#
# The leading `exec` matters: without it the shell stays alive as PID 1 with
# uvicorn as its child, and SIGTERM (docker stop, or a Render redeploy) goes
# to the shell instead of uvicorn -- so the server never shuts down cleanly
# and gets SIGKILLed after the grace period, cutting live WebSockets. `exec`
# replaces the shell with uvicorn so it receives signals directly.
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
