FROM python:3.12-slim
WORKDIR /app
COPY demo_services/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY demo_services ./demo_services
EXPOSE 8004
CMD ["uvicorn", "demo_services.service:app", "--host", "0.0.0.0", "--port", "8004"]
