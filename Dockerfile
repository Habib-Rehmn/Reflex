FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
RUN python -c "import tomllib,subprocess; p=tomllib.load(open('pyproject.toml','rb')); subprocess.check_call(['pip','install','--no-cache-dir',*p['project']['dependencies']])"
RUN useradd --uid 10001 --create-home reflex && mkdir /app/data && chown reflex:reflex /app/data
COPY app ./app
USER reflex
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2)"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
