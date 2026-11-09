FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

# 国内服务器拉包慢时,构建时传 --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_INDEX_URL=https://pypi.org/simple
# 封面由程序排版再截图:需要无界面浏览器和中文字体
RUN apt-get update && apt-get install -y --no-install-recommends chromium-headless-shell fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -i "$PIP_INDEX_URL" -r requirements.txt

COPY app ./app
COPY static ./static
COPY docs ./docs
COPY tools ./tools
COPY evals ./evals

# 数据库、生成的图片、导出包、密钥都在 /app/data,运行时挂载出来持久化
VOLUME ["/app/data"]
EXPOSE 18765

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:18765/api/session',timeout=4)"

# 只能单进程运行:采集端心跳和后台任务队列都在进程内存里
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "18765", "--proxy-headers", "--forwarded-allow-ips", "*"]
