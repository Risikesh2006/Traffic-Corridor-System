FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY core ./core
COPY sim ./sim
COPY safety ./safety
COPY controller ./controller
COPY state ./state
COPY tracking ./tracking
COPY routing ./routing
COPY corridor ./corridor
COPY events ./events
COPY health ./health
COPY chaos ./chaos
COPY api ./api

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
