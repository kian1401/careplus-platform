# تصویر اجرایی MVP «سرویسا / Care+» — سبک، بدون وابستگی خارجی، سازگار با Render/هر Docker host
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Tehran

WORKDIR /app

# لایه وابستگی‌ها جدا کش می‌شود تا بیلدهای بعدی سریع بمانند
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# کد برنامه + دارایی‌های استاتیک کنسول
COPY app ./app
COPY demo.py ./
COPY tests ./tests

# داده دمو روی مسیر قابل‌نوشتن (در Render می‌توان دیسک دائمی به /data وصل کرد)
ENV CP_ENV=demo \
    CP_DATABASE_URL=sqlite:////data/careplus.sqlite
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
