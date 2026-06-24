# Tambaqui 🐟

IA brasileira de código, self-hosted, com API OpenAI-compatível. Baixa modelos do HuggingFace,
roda local (CPU/CUDA/MPS), serve chat web + admin + streaming.

## Stack
- **Backend:** Python 3.11, FastAPI + uvicorn, tudo num único `app.py` (~1960 linhas).
- **Inferência (atual):** `transformers` + `torch` puro, `float16`, **sem quantização** (ver `ROADMAP.md`).
- **Frontend:** HTML/CSS/JS vanilla em `static/` (tema shadcn dark, sem framework).
- **Deploy:** Docker (`Dockerfile`, `docker-compose.yml`), templates CasaOS e Unraid, `install.sh`.

## Estrutura
- `app.py` — servidor inteiro: ModelManager (download/load/inferência), API OpenAI (`/v1/...`),
  API interna (`/api/...`), auth/users, sessões, busca web, CLI.
- `static/` — `chat.html`, `admin.html`, `login.html`.
- `modelos/` — modelos HuggingFace baixados. `dados/` — users, config, sessões.
- `CATALOGO` (em `app.py`) — modelos recomendados hardcoded (Qwen2.5-Coder, DeepSeek-Coder, CodeLlama).

## Rodar
```bash
python3 app.py          # servidor web (API + Admin) em :8000
python3 app.py chat     # chat no terminal
python3 app.py user criar <nome> <senha> [--admin]
```
- Chat: http://localhost:8000 · Admin: http://localhost:8000/admin · API: `/v1/chat/completions`

## Endpoints principais
- OpenAI-compat: `POST /v1/chat/completions`, `POST /v1/completions`, `GET /v1/models`
- Interno: `/api/chat` (chat com busca web + sessão), `/api/modelos`, `/api/status`, `/api/auth/*`

## Notas importantes
- **`ROADMAP.md`** tem o plano de evolução (6 ondas) e os bugs conhecidos. Consulte antes de mexer.
- Bugs sérios conhecidos: geração bloqueia o event loop async; `_lock` declarado mas não usado;
  anti-alucinação não chega ao caminho `/v1/chat/completions`; `req.stop` ignorado.
- Direção acordada: migrar a engine p/ GGUF (llama.cpp) — destrava quantização, acesso a modelos e batching.
