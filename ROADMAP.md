# 🐟 Tambaqui — Roadmap / Plano Mestre

> **STATUS: Ondas 0–5 implementadas e verificadas** (py_compile + smoke tests + UI screenshots).
> Pendências documentadas: continuous batching real e tool calling exigem o engine externo
> (llama-server `--parallel` / vLLM) — a arquitetura já suporta o Tambaqui como proxy.
> Itens que precisam de teste em runtime na máquina do usuário: inferência transformers (sem
> `transformers` instalado no ambiente de dev) e GGUF (sem `llama-cpp-python` instalado).

> Plano consolidado de evolução do Tambaqui, organizado em 6 ondas.
> As ondas 0–3 **não trocam a engine** (baixo risco, ganho rápido); a partir da 4 é o trabalho estrutural.
> Origem: auditoria multi-agente (performance, anti-alucinação, acesso a modelos, multiusuário) +
> comparação com Ollama, LM Studio, Jan, llama.cpp, vLLM, LocalAI + mudanças do contribuinte (GPU NVIDIA).

## Diagnóstico em uma linha

As 4 dores — **performance lenta, modelos pequenos que alucinam, acesso a todos os modelos, multiusuário** —
têm uma **causa raiz comum: a escolha da engine**. Hoje o Tambaqui usa `transformers` puro em `float16`
**sem quantização** — o piso do mercado. Um 7B fp16 não cabe em 16GB → o usuário é forçado a um 0.5B que
alucina → daí a engenharia de prompt/regex anti-alucinação que ataca o sintoma. Com GGUF Q4_K_M, esse mesmo
7B roda em ~4-5GB e alucina muito menos. **Boa parte do problema de alucinação é um problema de quantização disfarçado.**

## 3 bugs sérios já identificados

1. **O servidor congela inteiro durante cada geração** — `chat_completions` é `async` mas chama `manager.gerar()`
   síncrono → trava o event loop (health, /admin, /v1/models, outros usuários). `app.py:1551`
2. **O `_lock` é declarado e nunca usado** — dois requests paralelos corrompem o KV-cache do modelo único. `app.py:422`
3. **O anti-alucinação é inerte via API** — `SYSTEM_PROMPT` / `_reforcar_system_prompt` só agem nos endpoints
   internos; pelo `/v1/chat/completions` (aider/cline/Continue) nada chega ao modelo. E `req.stop` é ignorado.

---

## 🟢 Onda 0 — GPU NVIDIA (contribuinte + `--gpus all`)

> Causa do "não via a NVIDIA": o bug `total_mem`. O atributo correto do PyTorch é `total_memory`.
> Como estava dentro de `try/except: pass`, falhava em silêncio e caía pra CPU.
> Contribuinte: **caiquebrito** (tem placa NVIDIA, achou e corrigiu).

| # | Ação | Local | Esforço |
|---|---|---|---|
| 0a | **`total_mem` → `total_memory`** (3 ocorrências) | `app.py:221`, `app.py:437`, `app.py:674` | baixo |
| 0b | **Fallback `nvidia-smi`** — detecta GPU mesmo sem CUDA no torch e avisa "rebuild necessário" | `detectar_devices()` | baixo |
| 0c | **Dockerfile CUDA** (base `nvidia/cuda:12.1.1-cudnn8`, torch `cu121`) como tag `:cuda`, mantendo `:latest` slim p/ CPU | `Dockerfile` + `Dockerfile.cuda` | baixo |
| 0d | **`--gpus all` nos templates** (compose/unraid/casaos) | templates | baixo |
| 0e | **Reconciliar `torch.compile`** — só `reduce-overhead` em CUDA; **desligar em CPU** (não copiar o `device != "mps"` do contribuinte) | `carregar()` | baixo |
| 0f | ⚠️ **NÃO copiar** a remoção do `SanitizeLogitsProcessor`/`top_k`/clamp de temperatura do contribuinte — tratado na Onda 1 | `_get_gen_kwargs` | — |

### Onde vai o `--gpus all`

```yaml
# docker-compose.yml — forma moderna e portável (equivale a --gpus all)
services:
  tambaqui:
    image: ghcr.io/koalitos/tambaqui:cuda
    deploy:
      resources:
        reservations:
          devices:
            - { driver: nvidia, count: all, capabilities: [gpu] }
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
      - NVIDIA_DRIVER_CAPABILITIES=compute,utility
```

```xml
<!-- unraid-template.xml — o "extra parameter" -->
<ExtraParams>--gpus all</ExtraParams>
<Config Name="NVIDIA Visible" Target="NVIDIA_VISIBLE_DEVICES" Default="all" Type="Variable" Display="advanced">all</Config>
```

```bash
docker run --gpus all -p 8000:8000 ghcr.io/koalitos/tambaqui:cuda
```

> **Imagem:** publicar 2 tags — `:latest` (slim, CPU) e `:cuda` (NVIDIA). A imagem CUDA é pesada (~vários GB);
> não forçar quem não tem GPU.

### Mudanças do contribuinte — parecer

| Mudança | Veredito |
|---|---|
| `total_mem` → `total_memory` (3x) | ✅ pegar como está (bug fix que destrava NVIDIA) |
| Fallback `nvidia-smi` em `detectar_devices` | ✅ pegar (ótima UX) |
| Dockerfile CUDA + compose `runtime: nvidia` / env NVIDIA | ✅ pegar, manter variante CPU separada |
| `torch.compile` em CPU (`device != "mps"`) | ⚠️ adaptar — `reduce-overhead` é p/ GPU; desligar em CPU |
| Removeu `SanitizeLogitsProcessor` + `top_k` + clamp temp | ⚠️ discutir — tira proteção anti-NaN do fp16 e conflita com anti-alucinação |

---

## 🟡 Onda 1 — Quick-wins de backend (perf + anti-alucinação + não-congelar)

| # | Frente | Ação |
|---|---|---|
| 1a | multiusuário | **Tirar `model.generate` do event loop** (`run_in_threadpool`) + **usar de fato o `_lock`** (ou `Semaphore(1)`) |
| 1b | alucinação | Modelos ≤3B: **greedy (`do_sample=False`)** por padrão; ao amostrar, `min_p=0.05` + `no_repeat_ngram_size=3`. Decidir pelo **nº real de params**, não por substring. **Manter** o anti-NaN como singleton (não recriar por token) |
| 1c | alucinação | **Encurtar o SYSTEM_PROMPT** (~1500 tok → 2-4 frases, sem few-shot que causa eco) e **injetá-lo no `/v1/chat/completions`** quando o request não trouxer `role=system` |
| 1d | performance | **Respeitar `req.stop`** (`stop_strings` + `tokenizer`), `max_tokens` default 2048→512 (~64 no caminho casual), timeout por request, detectar `request.is_disconnected()` |
| 1e | performance | `attn_implementation="sdpa"` no lugar do `to_bettertransformer()` (deprecated, falha silenciosa); `usage` com tokens reais do tokenizer |
| 1f | multiusuário | Fila FIFO com `Semaphore` + backpressure (429/503 com `Retry-After`) |

---

## 🔵 Onda 2 — Acesso a modelos + busca no HuggingFace

| # | Ação |
|---|---|
| 2a | **🔎 Busca no HF Hub na UI**: `HfApi().list_models(search=, filter="text-generation", sort="downloads")` → cards (nome, downloads, likes, tamanho) p/ escolher e baixar com 1 clique |
| 2b | Colar `org/modelo` arbitrário (o backend `baixar()` já aceita repo livre — só falta UI) |
| 2c | Validar arquitetura antes de baixar (`architectures`/`pipeline_tag`) + `trust_remote_code` **opt-in** (fecha um RCE) |
| 2d | `snapshot_download` com `allow_patterns`/`ignore_patterns` (parar de baixar o repo HF inteiro) |
| 2e | Sugerir quantização conforme RAM/VRAM detectada antes do download |

---

## 🟣 Onda 3 — Redesign da UI (animações, listas, disposição)

> Base shadcn dark é boa; falta vida e hierarquia. Tudo vanilla (sem framework, p/ não inflar o container).

**Global**
- Micro-animações (transições `cubic-bezier`, hover com leve elevação/escala, fade/slide ao trocar de aba).
- Skeleton loaders no lugar de telas vazias (status, modelos, sessões).
- Toasts animados (slide-in + auto-dismiss) no lugar de `alert()`.
- Barras de progresso com shimmer no download.
- Acento de cor da marca (verde tambaqui) em foco/ativos.

**Admin** (hoje: pilha vertical de ~8 cards)
- Reorganizar em **abas** com nav fixa: `Status · Modelos · Busca HF · Device · Energia · API · Usuários`.
- **Grid responsivo** de modelos com badge de status (baixado/ativo/baixando) e ação no hover.
- Stats de sistema/GPU com contadores animados + mini-sparkline de CPU/RAM.

**Chat**
- Sidebar de sessões agrupada por data (Hoje/Ontem/Semana) com animação de entrada/saída.
- Streaming com cursor pulsante, auto-scroll suave, copiar com feedback animado.
- Empty-state ilustrado.

**Login**
- Card centralizado, fundo animado sutil, foco automático.

---

## 🟠 Onda 4 — Engine GGUF (estrutural — destrava as 4 frentes)

> **Decisão arquitetural central:** parar de usar `transformers` puro como engine de produção.
> Migrar p/ **llama.cpp (`llama-cpp-python`/`llama-server`)** como backend primário em CPU/Apple Silicon;
> **vLLM** opcional em GPU NVIDIA; `transformers` como fallback de compatibilidade.
> Resolve de uma vez: quantização, GGUF, GPU offload por camadas, prefix caching, constrained decoding e batching.

| # | Ação |
|---|---|
| 4a | `llama-cpp-python` (GGUF) como engine primária; vLLM opcional em GPU NVIDIA; `transformers` fallback |
| 4b | Detectar `.gguf` em `listar_locais()` e **rotear `gerar()`** pro backend correto |
| 4c | Baixar **um único `.gguf`** (`bartowski/*`, `*-GGUF`) + sugestão de quant (Q4_K_M/Q5_K_M) |
| 4d | **GBNF grammar / JSON Schema** (anti-alucinação a nível de token) + expor `response_format` |
| 4e | **Remover o `_validar_resposta`** (regex que corrompe código real e nem roda no streaming) |

---

## 🔴 Onda 5 — Multiusuário real + paridade de mercado

| # | Ação |
|---|---|
| 5a | Concorrência real: `llama-server --parallel N` (CPU/Mac) ou vLLM (continuous batching + PagedAttention). Tambaqui vira proxy de admin/auth/rate-limit na frente do engine OpenAI-compatível |
| 5b | Sessões/tokens/logs em **SQLite/Redis** com TTL (hoje em dict de RAM, somem no restart, não escalam multi-worker) |
| 5c | **Rate limiting** por API key/IP |
| 5d | Endpoint **`/v1/embeddings`** + **tool/function calling** (`tool_calls`) |

---

## Matriz de gaps vs. mercado (resumo)

| Feature | Tem? | Quem tem | Impacto |
|---|---|---|---|
| Quantização (GGUF/k-quants, GPTQ, AWQ, bnb, MLX) | não | Ollama, LM Studio, Jan, llama.cpp, vLLM, LocalAI | crítico |
| Formato GGUF | não | idem | crítico |
| Geração fora do event loop (não congelar) | não | todos | crítico |
| Lock/serialização real do modelo | não (declarado, não usado) | todos | crítico |
| Continuous batching | não | vLLM, llama.cpp `--parallel`, Ollama | alto |
| Constrained decoding (GBNF/JSON Schema) | não | llama.cpp, Ollama, LM Studio, vLLM | alto |
| Prefix/prompt caching | não | vLLM, llama.cpp | alto |
| Respeitar `stop` | não | todos | alto |
| GPU offload por camadas (`n-gpu-layers`) | parcial | llama.cpp, Ollama, LM Studio | alto |
| MLX em Apple Silicon | não | LM Studio, Jan, LocalAI | alto |
| Busca de modelos no HF Hub na UI | parcial (catálogo fixo) | LM Studio, Jan, GPT4All | alto |
| Fila + backpressure | não | Ollama, vLLM, LocalAI | alto |
| Tool/function calling | não | Ollama, LM Studio, vLLM, LocalAI | médio |
| `/v1/embeddings` | não | Ollama, LM Studio, LocalAI | médio |
| RAG/grounding local | parcial (busca web) | LM Studio, Jan, GPT4All | médio |
| SDPA/FlashAttention confiável | parcial (`to_bettertransformer` deprecated) | todos | médio |
| Sessões persistentes (sobrevivem a restart) | não | gateways de produção | médio |

---

## Ordem recomendada

**0 → 1 → 2 → 3** primeiro (rápido, visível, baixo risco), depois **4 → 5** (estrutural).
A Onda 0a (`total_memory`) é literalmente 3 linhas que consertam NVIDIA pra todo mundo.
