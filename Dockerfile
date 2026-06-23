FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

RUN mkdir -p modelos dados/sessoes && chmod -R 777 modelos dados

# Criar user admin padrão se não existir (pode trocar senha pelo admin web)
ENV TAMBAQUI_ADMIN_USER=admin
ENV TAMBAQUI_ADMIN_PASS=tambaqui

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/status || exit 1

# Entrypoint: cria admin se não existe + inicia server
CMD python3 -c "\
from app import user_criar, _carregar_users; \
import os; \
users = _carregar_users(); \
u = os.environ.get('TAMBAQUI_ADMIN_USER', 'admin'); \
p = os.environ.get('TAMBAQUI_ADMIN_PASS', 'tambaqui'); \
user_criar(u, p, True) if u not in users else None; \
" && python3 app.py
