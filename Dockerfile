FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY webapp webapp
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "app.main"]
