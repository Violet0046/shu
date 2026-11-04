FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 SHU_WEB_DATA_DIR=/data SHU_WEB_HOST=0.0.0.0
WORKDIR /app
COPY pyproject.toml README.md ./
COPY shu_grading ./shu_grading
COPY web_app.py ./
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home shu && mkdir -p /data && chown shu:shu /data
USER shu
EXPOSE 8000
CMD ["python", "web_app.py", "--api-only"]
