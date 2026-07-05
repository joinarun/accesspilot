# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

FROM node:20-bookworm-slim AS frontend-builder

WORKDIR /build
COPY ./frontend/package.json ./frontend/package-lock.json ./frontend/
RUN npm ci --prefix frontend
COPY ./frontend ./frontend
RUN npm run build --prefix frontend

FROM node:20-bookworm-slim AS node-runtime

FROM python:3.12-slim-bookworm

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

RUN pip install --no-cache-dir uv==0.8.13

WORKDIR /code

COPY ./pyproject.toml ./README.md ./uv.lock* ./
COPY ./app ./app
COPY ./scanner/package.json ./scanner/package-lock.json ./scanner/
RUN npm ci --prefix scanner \
    && cd scanner \
    && npx playwright install --with-deps chromium
COPY ./scanner/src ./scanner/src
COPY ./examples/vulnerable-app ./examples/vulnerable-app
COPY --from=frontend-builder /build/frontend/dist ./frontend/dist

RUN uv sync --frozen --no-dev --no-install-project \
    && apt-get update \
    && apt-get install -y --no-install-recommends git gh \
    && rm -rf /var/lib/apt/lists/*

ARG COMMIT_SHA=""
ENV COMMIT_SHA=${COMMIT_SHA}

ARG AGENT_VERSION=0.0.0
ENV AGENT_VERSION=${AGENT_VERSION}

ENV ACCESSPILOT_PUBLIC_BASE_URL=http://127.0.0.1:8080

EXPOSE 8080

CMD ["/code/.venv/bin/uvicorn", "app.fast_api_app:app", "--host", "0.0.0.0", "--port", "8080"]
