#!/usr/bin/env python3
"""
Tambaqui - IA Especialista em Codificação
API OpenAI-compatível + Admin Web + Download de Modelos HuggingFace

  python3 app.py              # Servidor (API + Admin)
  python3 app.py chat         # Chat CLI
  curl http://localhost:8000/v1/models  # Listar modelos
"""

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import hashlib
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import List, Optional, Generator, Union

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, ConfigDict

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

import secrets
import sqlite3

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("tambaqui")

# ============================================================
# CONFIG
# ============================================================

TAMBAQUI_DIR = Path(os.environ.get("TAMBAQUI_DIR", "."))
MODELOS_DIR = TAMBAQUI_DIR / "modelos"
DADOS_DIR = TAMBAQUI_DIR / "dados"
SESSOES_DIR = DADOS_DIR / "sessoes"
for d in [MODELOS_DIR, DADOS_DIR, SESSOES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

HOST = os.environ.get("TAMBAQUI_HOST", "0.0.0.0")
PORT = int(os.environ.get("TAMBAQUI_PORT", "8000"))

SYSTEM_PROMPT = (
    # ─────────────────────────────────────────────────────────────
    # IDENTIDADE
    # ─────────────────────────────────────────────────────────────
    "Você é o Tambaqui, uma IA brasileira especialista em programação e "
    "desenvolvimento de software. Seu papel é ajudar pessoas a escrever, entender, "
    "depurar e melhorar código. Você é direto, técnico e didático, sem ser arrogante. "
    "Trata iniciantes com paciência e devs experientes sem subestimá-los.\n\n"

    # ─────────────────────────────────────────────────────────────
    # IDIOMA E FORMATAÇÃO
    # ─────────────────────────────────────────────────────────────
    "IDIOMA E FORMATAÇÃO:\n"
    "- Responda SEMPRE em português brasileiro, mesmo que o código e os termos técnicos "
    "estejam em inglês (nomes de variáveis, funções e bibliotecas ficam em inglês por convenção).\n"
    "- Todo código vai em bloco markdown com a linguagem declarada: ```python, ```js, ```sql, etc.\n"
    "- Quando o código pertence a um arquivo específico, indique o nome do arquivo antes do bloco "
    "(ex: 'arquivo: app.py').\n"
    "- Para comandos de terminal, use ```bash.\n"
    "- Não floreie demais: vá ao ponto. Texto longo só quando a explicação exige.\n\n"

    # ─────────────────────────────────────────────────────────────
    # CONVERSA CASUAL vs PEDIDO TÉCNICO
    # ─────────────────────────────────────────────────────────────
    "DISTINÇÃO ENTRE CONVERSA E CÓDIGO:\n"
    "- Cumprimentos e papo casual NÃO viram código. Responda como assistente normal.\n"
    "  - 'oi' / 'boa tarde' / 'tudo bem?' -> 'Boa tarde! Como posso ajudar com código hoje?'\n"
    "  - 'valeu' / 'obrigado' -> agradeça de volta, de forma breve.\n"
    "- Só gere código quando o usuário PEDIR de forma explícita com verbos como: "
    "crie, faça, gere, implemente, monte, escreva, corrija, refatore, otimize.\n"
    "  - 'crie um app que mostra boa tarde' -> pedido técnico, gere o código.\n"
    "  - 'como funciona um loop?' -> pergunta conceitual, EXPLIQUE (com exemplo curto), não monte um app inteiro.\n"
    "- Se o pedido for vago ('me ajuda com Python', 'faz aí'), pergunte o que a pessoa "
    "quer ANTES de gerar: 'Quer que eu explique o conceito, mostre um exemplo ou gere um código completo?'\n\n"

    # ─────────────────────────────────────────────────────────────
    # QUALIDADE DO CÓDIGO
    # ─────────────────────────────────────────────────────────────
    "PADRÕES DE QUALIDADE DE CÓDIGO:\n"
    "- Gere código COMPLETO e funcional — nada de '...' ou 'resto do código aqui'. "
    "A pessoa deve conseguir copiar e rodar.\n"
    "- Inclua os imports necessários e, quando útil, o comando para instalar dependências "
    "(ex: 'pip install requests').\n"
    "- Use nomes de variáveis claros e siga as convenções da linguagem (snake_case em Python, "
    "camelCase em JS, etc.).\n"
    "- Adicione comentários nos pontos não óbvios, mas não comente o trivial.\n"
    "- Trate erros previsíveis (try/except, validação de entrada, checagem de None/null) "
    "quando o contexto pedir.\n"
    "- Prefira soluções simples e legíveis a soluções 'espertas' e ilegíveis.\n"
    "- Se houver mais de uma abordagem razoável, escolha a mais comum e mencione a alternativa em uma linha.\n\n"

    # ─────────────────────────────────────────────────────────────
    # SEGURANÇA E BOAS PRÁTICAS
    # ─────────────────────────────────────────────────────────────
    "SEGURANÇA E BOAS PRÁTICAS:\n"
    "- Nunca coloque senhas, tokens ou chaves de API direto no código: use variáveis de ambiente "
    "(os.getenv) e explique isso.\n"
    "- Em SQL, use queries parametrizadas (nunca concatene input do usuário direto na query).\n"
    "- Alerte sobre riscos quando relevante (injeção, dados sensíveis, eval em input externo).\n"
    "- Não escreva código malicioso (malware, exploits, scrapers que violam termos de uso). "
    "Se pedirem, recuse de forma educada e explique o porquê.\n\n"

    # ─────────────────────────────────────────────────────────────
    # ESTRUTURA DA RESPOSTA TÉCNICA
    # ─────────────────────────────────────────────────────────────
    "ESTRUTURA AO ENTREGAR CÓDIGO:\n"
    "1. Uma frase explicando o que o código faz.\n"
    "2. O bloco de código completo.\n"
    "3. Como rodar (comando, dependências) — apenas se não for óbvio.\n"
    "4. Observações ou próximos passos, se houver — de forma curta.\n\n"

    # ─────────────────────────────────────────────────────────────
    # DEPURAÇÃO
    # ─────────────────────────────────────────────────────────────
    "AO AJUDAR A DEPURAR (debug):\n"
    "- Peça a mensagem de erro completa e o trecho relevante, se a pessoa não enviou.\n"
    "- Explique a CAUSA do erro, não só a correção.\n"
    "- Mostre o trecho corrigido (não precisa reescrever o arquivo inteiro se for só uma parte).\n\n"

    # ─────────────────────────────────────────────────────────────
    # HONESTIDADE / ANTI-ALUCINAÇÃO
    # ─────────────────────────────────────────────────────────────
    "HONESTIDADE:\n"
    "- NUNCA invente funções, bibliotecas, parâmetros ou comportamentos que não existem.\n"
    "- Se não tiver certeza, diga claramente: 'Não tenho certeza, confirme na documentação oficial.'\n"
    "- Se algo depende da versão da linguagem ou da lib, mencione a versão considerada.\n"
    "- Se o que a pessoa quer está desatualizado ou tem uma forma melhor hoje, avise.\n"
    "- Quando não souber, diga onde pesquisar (docs oficiais, MDN, Stack Overflow, etc.).\n\n"

    # ─────────────────────────────────────────────────────────────
    # EXEMPLOS DE COMPORTAMENTO
    # ─────────────────────────────────────────────────────────────
    "EXEMPLOS DE COMO SE COMPORTAR:\n"
    "Usuário: 'boa tarde'\n"
    "Tambaqui: 'Boa tarde! Como posso ajudar com código hoje?'\n\n"
    "Usuário: 'o que é uma API?'\n"
    "Tambaqui: [explica o conceito em poucas linhas, com um exemplo simples; pergunta se quer ver código]\n\n"
    "Usuário: 'crie uma API REST de tarefas em Flask'\n"
    "Tambaqui: [frase do que faz + código completo e rodável + como executar]\n\n"
    "Usuário: 'tá dando erro aqui ó' (sem mostrar o erro)\n"
    "Tambaqui: 'Manda a mensagem de erro completa e o trecho do código pra eu ver o que está acontecendo.'\n"
)

# Prompt enxuto pra modelos pequenos (<=3.5B): prompt longo faz eles perderem aderência e ecoarem.
SYSTEM_PROMPT_CURTO = (
    "Você é o Tambaqui, uma IA brasileira especialista em programação. "
    "Responda SEMPRE em português brasileiro, de forma direta e objetiva. "
    "Todo código vai em bloco markdown com a linguagem declarada (```python, ```js, ```sql, ...). "
    "Gere código completo e funcional, com os imports necessários — nada de '...'. "
    "Cumprimento ou papo casual (oi, boa tarde, valeu): responda em UMA linha, SEM código. "
    "Pergunta conceitual: explique curto, com um exemplo. "
    "Nunca invente funções, bibliotecas ou parâmetros que não existem; se não tiver certeza, diga."
)


def _get_system_prompt(n_params: float = None) -> str:
    """Prompt curto pra modelos pequenos (<=3.5B); completo pros maiores.
    n_params = nº real de parâmetros do modelo ativo (use manager.n_params)."""
    if n_params and n_params <= 3.5e9:
        return SYSTEM_PROMPT_CURTO
    return SYSTEM_PROMPT

HTTP_HEADERS = {"User-Agent": "Tambaqui/2.0"}

# Catálogo de modelos recomendados
CATALOGO = {
    "qwen2.5-coder-0.5b": {
        "repo": "Qwen/Qwen2.5-Coder-0.5B-Instruct",
        "ram": "1 GB", "desc": "Ultra leve - qualquer PC", "params": "0.5B",
    },
    "qwen2.5-coder-1.5b": {
        "repo": "Qwen/Qwen2.5-Coder-1.5B-Instruct",
        "ram": "3 GB", "desc": "Leve - PCs fracos", "params": "1.5B",
    },
    "qwen2.5-coder-3b": {
        "repo": "Qwen/Qwen2.5-Coder-3B-Instruct",
        "ram": "6 GB", "desc": "Balanceado", "params": "3B",
    },
    "qwen2.5-coder-7b": {
        "repo": "Qwen/Qwen2.5-Coder-7B-Instruct",
        "ram": "14 GB", "desc": "Completo", "params": "7B",
    },
    "deepseek-coder-1.3b": {
        "repo": "deepseek-ai/deepseek-coder-1.3b-instruct",
        "ram": "3 GB", "desc": "Leve - código", "params": "1.3B",
    },
    "deepseek-coder-6.7b": {
        "repo": "deepseek-ai/deepseek-coder-6.7b-instruct",
        "ram": "14 GB", "desc": "Completo - código", "params": "6.7B",
    },
    "codellama-7b": {
        "repo": "codellama/CodeLlama-7b-Instruct-hf",
        "ram": "14 GB", "desc": "Meta - código", "params": "7B",
    },
}


# ============================================================
# HELPERS DE MODELO (HUB / DOWNLOAD)
# ============================================================


def _estimar_tamanho_nome(mid: str) -> str:
    """Extrai o nº de params do nome do modelo (ex.: '...-7B-...' -> '7B'). Heurística."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*[bB](?![a-zA-Z])", mid.replace("-", " ").replace("_", " "))
    return f"{m.group(1)}B" if m else ""


def _params_do_nome(s: str) -> float:
    """Nº de params (float) a partir do nome (ex.: 'Qwen2.5-Coder-7B' -> 7e9). 0 se desconhecido."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*[bB](?![a-zA-Z])", (s or "").replace("-", " ").replace("_", " "))
    return float(m.group(1)) * 1e9 if m else 0


def _eh_repo_gguf(mid: str) -> bool:
    return "gguf" in (mid or "").lower()


# Formatos de outros frameworks (TF/Flax/ONNX) e GGUF — não baixar no caminho transformers.
_FORMATOS_IGNORADOS = (".onnx", ".onnx_data", ".msgpack", ".h5", ".tflite", ".ot", ".gguf")


def _arquivo_desnecessario(fname: str) -> bool:
    return fname.lower().endswith(_FORMATOS_IGNORADOS)


def _repo_eh_geracao_texto(info) -> bool:
    """Heurística: o repo é um modelo de geração de texto carregável por AutoModelForCausalLM?"""
    pt = getattr(info, "pipeline_tag", None)
    try:
        arch = (getattr(info, "config", None) or {}).get("architectures", []) or []
    except Exception:
        arch = []
    a = " ".join(arch)
    if any(k in a for k in ("CausalLM", "ForConditionalGeneration", "LMHeadModel")):
        return True
    if pt in ("text-generation", "text2text-generation"):
        return True
    # Bloqueia só quando há um pipeline_tag EXPLÍCITO de outra modalidade (imagem, áudio, etc.).
    OUTRAS = {"text-to-image", "image-to-text", "automatic-speech-recognition", "text-to-speech",
              "image-classification", "object-detection", "feature-extraction", "sentence-similarity",
              "fill-mask", "token-classification", "text-classification", "translation", "summarization"}
    if pt in OUTRAS:
        return False
    return True  # metadados ausentes: permitir (não bloquear modelo legítimo sem pipeline_tag)


# ============================================================
# SISTEMA
# ============================================================


def get_system_info() -> dict:
    info = {"platform": platform.system(), "python": platform.python_version(), "cpu_count": os.cpu_count() or 0}
    if HAS_PSUTIL:
        mem = psutil.virtual_memory()
        info["ram_total_gb"] = round(mem.total / (1024**3), 1)
        info["ram_usada_gb"] = round(mem.used / (1024**3), 1)
        info["ram_percent"] = mem.percent
        info["cpu_percent"] = psutil.cpu_percent(interval=0.1)
        proc = psutil.Process(os.getpid())
        info["proc_ram_mb"] = round(proc.memory_info().rss / (1024**2), 1)
    # GPU
    info["gpus"] = []
    try:
        import torch
        if torch.cuda.is_available():
            for i in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(i)
                total = props.total_memory
                usado = torch.cuda.memory_allocated(i)
                reservado = torch.cuda.memory_reserved(i)
                info["gpus"].append({
                    "id": i, "nome": props.name,
                    "vram_total_gb": round(total / (1024**3), 1),
                    "vram_usada_gb": round(usado / (1024**3), 1),
                    "vram_reservada_gb": round(reservado / (1024**3), 1),
                    "vram_percent": round(usado / total * 100, 1) if total else 0,
                    "tipo": "cuda",
                })
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            # MPS não tem API de memória detalhada, mas podemos mostrar alocação
            mps_alloc = torch.mps.current_allocated_memory() if hasattr(torch.mps, "current_allocated_memory") else 0
            info["gpus"].append({
                "id": 0, "nome": "Apple GPU (MPS)",
                "vram_total_gb": 0,
                "vram_usada_gb": round(mps_alloc / (1024**3), 1),
                "vram_reservada_gb": 0,
                "vram_percent": 0,
                "tipo": "mps",
            })
    except Exception:
        pass
    return info


# ============================================================
# USERS - Autenticação simples
# ============================================================

USERS_FILE = DADOS_DIR / "users.json"
DB_FILE = DADOS_DIR / "tambaqui.db"


class TokenStore:
    """Tokens de login em SQLite — sobrevivem a restart e a múltiplos workers (antes era dict em RAM)."""

    def __init__(self, path):
        self.path = str(path)
        self._lock = threading.Lock()
        self._exec("CREATE TABLE IF NOT EXISTS tokens (token TEXT PRIMARY KEY, username TEXT, expires REAL)")

    def _exec(self, sql, params=(), fetch=False):
        with self._lock:
            c = sqlite3.connect(self.path, check_same_thread=False)
            try:
                cur = c.execute(sql, params)
                row = cur.fetchone() if fetch else None
                c.commit()
                return row
            finally:
                c.close()

    def add(self, token: str, username: str, ttl: int = 86400 * 7):
        self._exec("INSERT OR REPLACE INTO tokens VALUES (?,?,?)", (token, username, time.time() + ttl))

    def get(self, token: str):
        row = self._exec("SELECT username, expires FROM tokens WHERE token=?", (token,), fetch=True)
        if not row:
            return None
        username, expires = row
        if expires and expires < time.time():
            self.delete(token)
            return None
        return username

    def delete(self, token: str):
        self._exec("DELETE FROM tokens WHERE token=?", (token,))

    def delete_user(self, username: str):
        self._exec("DELETE FROM tokens WHERE username=?", (username,))


_tokens = TokenStore(DB_FILE)


def _carregar_users() -> dict:
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text())
    return {}


def _salvar_users(users: dict):
    USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2))


def _hash_senha(senha: str) -> str:
    return hashlib.sha256(senha.encode()).hexdigest()


def _gerar_api_key() -> str:
    return "tb-" + secrets.token_hex(24)


def user_criar(username: str, senha: str, admin: bool = False) -> dict:
    users = _carregar_users()
    if username in users:
        return {"erro": f"User '{username}' já existe"}
    api_key = _gerar_api_key()
    users[username] = {
        "senha_hash": _hash_senha(senha),
        "admin": admin,
        "api_key": api_key,
        "criado_em": datetime.now().isoformat(),
    }
    _salvar_users(users)
    return {"ok": True, "username": username, "admin": admin, "api_key": api_key}


def user_login(username: str, senha: str) -> Optional[str]:
    users = _carregar_users()
    user = users.get(username)
    if not user or user["senha_hash"] != _hash_senha(senha):
        return None
    token = secrets.token_hex(32)
    _tokens.add(token, username)
    return token


def user_validar(token: str) -> Optional[dict]:
    # Sessão de login
    username = _tokens.get(token)
    if username:
        users = _carregar_users()
        user = users.get(username)
        if user:
            return {"username": username, "admin": user.get("admin", False)}

    # API key (tb-...)
    if token.startswith("tb-"):
        users = _carregar_users()
        for uname, data in users.items():
            if data.get("api_key") == token:
                return {"username": uname, "admin": data.get("admin", False)}

    return None


def user_trocar_senha(username: str, nova_senha: str) -> dict:
    users = _carregar_users()
    if username not in users:
        return {"erro": "User não encontrado"}
    users[username]["senha_hash"] = _hash_senha(nova_senha)
    _salvar_users(users)
    return {"ok": True}


def user_listar() -> list:
    users = _carregar_users()
    return [{"username": u, "admin": d.get("admin", False),
             "api_key": d.get("api_key", ""), "criado_em": d.get("criado_em", "")}
            for u, d in users.items()]


def user_regenerar_key(username: str) -> dict:
    users = _carregar_users()
    if username not in users:
        return {"erro": "User não encontrado"}
    new_key = _gerar_api_key()
    users[username]["api_key"] = new_key
    _salvar_users(users)
    return {"ok": True, "api_key": new_key}


def user_get_api_key(username: str) -> Optional[str]:
    users = _carregar_users()
    user = users.get(username)
    if not user:
        return None
    # Gerar se não tem
    if not user.get("api_key"):
        user["api_key"] = _gerar_api_key()
        _salvar_users(users)
    return user["api_key"]


def user_deletar(username: str) -> dict:
    users = _carregar_users()
    if username not in users:
        return {"erro": "User não encontrado"}
    del users[username]
    _salvar_users(users)
    _tokens.delete_user(username)  # revoga sessões do user deletado
    return {"ok": True}


def _get_user_from_request(request) -> Optional[dict]:
    """Extrai user do cookie ou header Authorization."""
    # Cookie
    token = request.cookies.get("tambaqui_token")
    if token:
        user = user_validar(token)
        if user:
            return user
    # Header: Authorization: Bearer <token>
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        user = user_validar(token)
        if user:
            return user
    return None


def _auth_ativo() -> bool:
    """Retorna True se tem users cadastrados (auth está ativo)."""
    return USERS_FILE.exists() and bool(_carregar_users())


# ============================================================
# MODEL MANAGER - Download, Load, Pre-warm
# ============================================================


CONFIG_FILE = DADOS_DIR / "config.json"


def _carregar_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def _salvar_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


class ModelManager:
    """Gerencia download, carregamento e inferência de modelos HuggingFace."""

    def __init__(self):
        self.model = None
        self.tokenizer = None
        self.modelo_ativo: Optional[str] = None
        self.device_atual: str = "auto"
        self.device_nome: str = ""  # cpu, cuda, mps
        self.carregando = False
        self.progresso_download = {}
        self.warmup_done = False
        self.n_params = 0            # nº real de parâmetros do modelo carregado
        self._sanitizer = None       # LogitsProcessorList anti-NaN (singleton, criado 1x)
        self.backend = "transformers"  # "transformers" (safetensors) ou "llama" (GGUF/llama.cpp)
        self.llama = None            # instância llama_cpp.Llama quando backend == "llama"
        self._lock = threading.Lock()  # serializa generate() — modelo único não é thread-safe

        # Carregar config salva
        cfg = _carregar_config()
        self.device_atual = cfg.get("device", "auto")

    def detectar_devices(self) -> list:
        """Lista devices disponíveis na máquina."""
        devices = [{"id": "cpu", "nome": "CPU", "disponivel": True, "info": f"{os.cpu_count()} cores"}]
        cuda_found = False

        try:
            import torch
            if torch.cuda.is_available():
                for i in range(torch.cuda.device_count()):
                    props = torch.cuda.get_device_properties(i)
                    mem_gb = round(props.total_memory / (1024**3), 1)
                    cuda_found = True
                    devices.append({
                        "id": f"cuda:{i}" if i > 0 else "cuda",
                        "nome": f"GPU NVIDIA: {props.name}",
                        "disponivel": True,
                        "info": f"{mem_gb} GB VRAM",
                    })
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                devices.append({"id": "mps", "nome": "GPU Apple (MPS)", "disponivel": True, "info": "Metal"})
        except Exception:
            pass

        # Fallback: detectar GPU NVIDIA via nvidia-smi quando o torch foi instalado sem CUDA.
        # Mostra a placa pro usuário com aviso de que precisa da imagem com CUDA (:cuda).
        if not cuda_found:
            try:
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=3,
                )
                if result.returncode == 0:
                    for i, line in enumerate(result.stdout.strip().splitlines()):
                        if not line.strip():
                            continue
                        parts = line.split(",")
                        nome = parts[0].strip()
                        mem_gb = round(int(parts[1].strip()) / 1024, 1) if len(parts) > 1 else 0
                        devices.append({
                            "id": f"cuda:{i}" if i > 0 else "cuda",
                            "nome": f"GPU NVIDIA: {nome}",
                            "disponivel": False,
                            "info": f"{mem_gb} GB VRAM — torch sem CUDA (use a imagem :cuda)",
                        })
            except Exception:
                pass

        devices.insert(0, {"id": "auto", "nome": "Automático", "disponivel": True, "info": "Detecta o melhor"})
        return devices

    def set_device(self, device: str) -> dict:
        """Define device preferido (auto, cpu, cuda, cuda:0, mps)."""
        self.device_atual = device
        cfg = _carregar_config()
        cfg["device"] = device
        _salvar_config(cfg)
        return {"ok": True, "device": device, "aviso": "Recarregue o modelo pra aplicar."}

    def get_load_mode(self) -> str:
        """Modo de carregamento: 'manual', 'preload', 'ondemand'."""
        cfg = _carregar_config()
        # Migrar config antiga
        if "preload" in cfg and "load_mode" not in cfg:
            return "preload" if cfg["preload"] else "manual"
        return cfg.get("load_mode", "manual")

    def set_load_mode(self, mode: str) -> dict:
        if mode not in ("manual", "preload", "ondemand"):
            return {"erro": "Modo inválido. Use: manual, preload, ondemand"}
        cfg = _carregar_config()
        cfg["load_mode"] = mode
        cfg.pop("preload", None)  # limpar config antiga
        _salvar_config(cfg)
        return {"ok": True, "load_mode": mode}

    def get_idle_timeout(self) -> int:
        cfg = _carregar_config()
        return cfg.get("idle_timeout_min", 0)

    def set_idle_timeout(self, minutes: int) -> dict:
        cfg = _carregar_config()
        cfg["idle_timeout_min"] = minutes
        _salvar_config(cfg)
        if minutes > 0:
            self._resetar_idle_timer()
        return {"ok": True, "idle_timeout_min": minutes}

    def get_api_mode(self) -> str:
        """Retorna modo da API: 'direto' (só modelo) ou 'busca' (modelo + web search)."""
        cfg = _carregar_config()
        return cfg.get("api_mode", "direto")

    def set_api_mode(self, mode: str) -> dict:
        """Define modo da API."""
        if mode not in ("direto", "busca"):
            return {"erro": "Modo inválido. Use: direto ou busca"}
        cfg = _carregar_config()
        cfg["api_mode"] = mode
        _salvar_config(cfg)
        return {"ok": True, "mode": mode}

    def get_trust_remote_code(self) -> bool:
        """trust_remote_code: default OFF (segurança). Necessário p/ alguns modelos com código próprio."""
        return bool(_carregar_config().get("trust_remote_code", False))

    def set_trust_remote_code(self, val: bool) -> dict:
        cfg = _carregar_config()
        cfg["trust_remote_code"] = bool(val)
        _salvar_config(cfg)
        return {"ok": True, "trust_remote_code": bool(val)}

    # --- Busca no HuggingFace Hub ---

    def buscar_hub(self, query: str, limit: int = 20) -> list:
        """Busca modelos de geração de texto no HuggingFace Hub (ordenados por downloads)."""
        try:
            from huggingface_hub import HfApi
            api = HfApi()
            locais = {m["nome"] for m in self.listar_locais()}
            resultados = []
            for m in api.list_models(search=query or "", filter="text-generation",
                                     sort="downloads", direction=-1, limit=limit):
                mid = m.id
                resultados.append({
                    "id": mid,
                    "downloads": getattr(m, "downloads", 0) or 0,
                    "likes": getattr(m, "likes", 0) or 0,
                    "gated": bool(getattr(m, "gated", False)),
                    "tamanho_hint": _estimar_tamanho_nome(mid),
                    "baixado": mid.split("/")[-1].lower() in locais,
                    "baixando": mid.split("/")[-1].lower() in self.progresso_download,
                })
            return resultados
        except Exception as e:
            logger.error(f"Erro na busca HF: {e}")
            return []

    def listar_gguf_repo(self, repo: str) -> list:
        """Lista os arquivos .gguf (quantizações) de um repo GGUF, do menor pro maior."""
        try:
            from huggingface_hub import HfApi
            info = HfApi().model_info(repo, files_metadata=True)
            files = [{"arquivo": s.rfilename, "tamanho_gb": round((s.size or 0) / (1024**3), 2)}
                     for s in info.siblings if s.rfilename.lower().endswith(".gguf")]
            files.sort(key=lambda x: x["tamanho_gb"])
            return files
        except Exception as e:
            logger.error(f"Erro listar gguf {repo}: {e}")
            return []

    def baixar_gguf(self, repo: str, filename: str) -> dict:
        """Baixa UM arquivo .gguf (uma quantização) — não o repo inteiro."""
        nome = repo.split("/")[-1].lower()
        if nome in self.progresso_download:
            return {"status": "Já está baixando..."}
        self.progresso_download[nome] = {"status": "iniciando", "percent": 1, "arquivo": filename, "velocidade": ""}

        def _dl():
            try:
                from huggingface_hub import hf_hub_download
                destino = MODELOS_DIR / nome
                destino.mkdir(parents=True, exist_ok=True)
                self.progresso_download[nome] = {"status": "baixando", "percent": 5, "arquivo": filename, "velocidade": "1 arquivo"}
                hf_hub_download(repo, filename, local_dir=str(destino))
                self.progresso_download[nome] = {"status": "concluído", "percent": 100, "arquivo": "", "velocidade": ""}
                logger.info(f"✅ GGUF baixado: {nome}/{filename}")
                time.sleep(3)
                del self.progresso_download[nome]
            except Exception as e:
                self.progresso_download[nome] = {"status": f"erro: {e}", "percent": -1, "arquivo": "", "velocidade": ""}
                logger.error(f"Erro ao baixar GGUF {repo}/{filename}: {e}")

        threading.Thread(target=_dl, daemon=True).start()
        return {"status": "Download iniciado", "nome": nome}

    # --- Descoberta ---

    def listar_locais(self) -> list:
        """Lista modelos já baixados (HF safetensors e GGUF/llama.cpp)."""
        modelos = []
        for d in sorted(MODELOS_DIR.iterdir()):
            # GGUF avulso (um arquivo .gguf solto na pasta modelos/)
            if d.is_file() and d.suffix.lower() == ".gguf":
                modelos.append({
                    "nome": d.name, "tamanho_gb": round(d.stat().st_size / (1024**3), 1),
                    "ativo": d.name == self.modelo_ativo, "path": str(d), "formato": "gguf",
                })
                continue
            if not d.is_dir():
                continue
            # Detectar formato
            has_gguf = any(d.glob("*.gguf"))
            has_safetensors = any(d.glob("*.safetensors"))
            has_config = (d / "config.json").exists()
            if not (has_safetensors or has_config or has_gguf):
                continue
            tamanho = sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / (1024**3)
            modelos.append({
                "nome": d.name,
                "tamanho_gb": round(tamanho, 1),
                "ativo": d.name == self.modelo_ativo,
                "path": str(d),
                "formato": "gguf" if has_gguf else "hf",
            })
        return modelos

    def _achar_gguf(self, nome: str):
        """Retorna o Path do arquivo .gguf de um modelo (avulso ou dentro da pasta), ou None."""
        p = MODELOS_DIR / nome
        if p.is_file() and p.suffix.lower() == ".gguf":
            return p
        if p.is_dir():
            ggufs = sorted(p.glob("*.gguf"))
            if ggufs:
                return ggufs[0]
        return None

    def listar_catalogo(self) -> list:
        """Lista modelos disponíveis para download."""
        locais = {m["nome"] for m in self.listar_locais()}
        catalogo = []
        for nome, info in CATALOGO.items():
            catalogo.append({
                "nome": nome,
                "repo": info["repo"],
                "ram": info["ram"],
                "desc": info["desc"],
                "params": info["params"],
                "baixado": nome in locais or any(nome.replace("-", "") in l.replace("-", "") for l in locais),
                "baixando": nome in self.progresso_download,
            })
        return catalogo

    # --- Download ---

    def baixar(self, nome: str) -> dict:
        """Baixa modelo do HuggingFace com progresso real."""
        if nome in self.progresso_download:
            return {"status": "Já está baixando..."}

        from_catalog = nome in CATALOGO
        if nome in CATALOGO:
            repo = CATALOGO[nome]["repo"]
            destino = MODELOS_DIR / nome
        else:
            repo = nome
            destino = MODELOS_DIR / nome.split("/")[-1].lower()
            nome = destino.name

        if destino.exists() and any(destino.glob("*.safetensors")):
            return {"status": "Modelo já existe", "nome": nome}

        self.progresso_download[nome] = {"status": "iniciando", "percent": 0, "arquivo": "", "velocidade": ""}

        def _download():
            try:
                from huggingface_hub import HfApi, hf_hub_download
                api = HfApi()

                # Listar arquivos do repo
                self.progresso_download[nome] = {"status": "listando arquivos", "percent": 1, "arquivo": "", "velocidade": ""}
                files = api.list_repo_files(repo)
                # Pular formatos de outros frameworks (TF/Flax/ONNX/GGUF) — economiza disco e banda.
                files = [f for f in files if not _arquivo_desnecessario(f)]
                total_files = len(files)
                destino.mkdir(parents=True, exist_ok=True)

                # Calcular tamanho total (se possível) + validar arquitetura de repos arbitrários
                info = api.model_info(repo)
                if not from_catalog and not _repo_eh_geracao_texto(info):
                    raise ValueError("Repo não parece um modelo de geração de texto (text-generation).")
                total_bytes = 0
                file_sizes = {}
                for s in info.siblings:
                    if s.size and not _arquivo_desnecessario(s.rfilename):
                        file_sizes[s.rfilename] = s.size
                        total_bytes += s.size

                baixado_bytes = 0

                for i, fname in enumerate(files):
                    arquivo_path = destino / fname
                    if arquivo_path.exists():
                        fsize = file_sizes.get(fname, arquivo_path.stat().st_size)
                        baixado_bytes += fsize
                        pct = int((baixado_bytes / total_bytes * 100)) if total_bytes else int((i + 1) / total_files * 100)
                        self.progresso_download[nome] = {
                            "status": "baixando", "percent": pct,
                            "arquivo": f"{fname} (já existe)",
                            "velocidade": f"{i+1}/{total_files} arquivos",
                        }
                        continue

                    fsize = file_sizes.get(fname, 0)
                    size_str = f" ({fsize / 1e9:.1f}GB)" if fsize > 1e9 else (f" ({fsize / 1e6:.0f}MB)" if fsize > 1e6 else "")
                    pct = int((baixado_bytes / total_bytes * 100)) if total_bytes else int(i / total_files * 100)
                    self.progresso_download[nome] = {
                        "status": "baixando", "percent": max(pct, 1),
                        "arquivo": f"{fname}{size_str}",
                        "velocidade": f"{i+1}/{total_files} arquivos",
                    }

                    # Baixar arquivo individual
                    hf_hub_download(repo, fname, local_dir=str(destino))
                    baixado_bytes += fsize

                self.progresso_download[nome] = {"status": "concluído", "percent": 100, "arquivo": "", "velocidade": ""}
                logger.info(f"✅ Modelo baixado: {nome}")
                time.sleep(3)
                del self.progresso_download[nome]

            except Exception as e:
                self.progresso_download[nome] = {"status": f"erro: {e}", "percent": -1, "arquivo": "", "velocidade": ""}
                logger.error(f"Erro ao baixar {nome}: {e}")

        threading.Thread(target=_download, daemon=True).start()
        return {"status": "Download iniciado", "nome": nome}

    def deletar(self, nome: str) -> dict:
        if nome == self.modelo_ativo:
            return {"erro": "Não pode deletar modelo ativo"}
        pasta = MODELOS_DIR / nome
        if pasta.exists():
            shutil.rmtree(pasta)
            return {"ok": True}
        return {"erro": "Modelo não encontrado"}

    # --- Carregamento (pre-load) ---

    def carregar(self, nome: str = None) -> dict:
        """Carrega modelo na RAM (pre-load)."""
        if self.carregando:
            return {"status": "Já está carregando..."}

        # Auto-detectar primeiro modelo disponível
        if not nome:
            locais = self.listar_locais()
            if not locais:
                return {"erro": "Nenhum modelo baixado. Baixe um primeiro."}
            nome = locais[0]["nome"]

        pasta = MODELOS_DIR / nome
        if not pasta.exists():
            return {"erro": f"Modelo '{nome}' não encontrado"}

        self.carregando = True
        logger.info(f"🧠 Pre-carregando: {nome}...")

        try:
            # GGUF -> engine llama.cpp (quantizado, roda em CPU fraca, GPU offload por camadas).
            # Demais formatos -> transformers.
            gguf_path = self._achar_gguf(nome)
            if gguf_path is not None:
                return self._carregar_gguf(nome, gguf_path)
            self.backend = "transformers"
            self.llama = None

            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            # trust_remote_code é opt-in (default OFF): executar código arbitrário do repo é um
            # vetor de RCE num servidor com login. Ligue em /admin só pra modelos confiáveis.
            trc = self.get_trust_remote_code()

            # Carregar tokenizer
            self.tokenizer = AutoTokenizer.from_pretrained(
                str(pasta), trust_remote_code=trc,
            )
            if not self.tokenizer.pad_token:
                self.tokenizer.pad_token = self.tokenizer.eos_token

            # Device e dtype
            # Tamanho dos pesos: safetensors OU bin/pt (modelos só-.bin davam 0 e furavam o fit check)
            _pesos = list(pasta.glob("*.safetensors")) or list(pasta.glob("*.bin")) or list(pasta.glob("*.pt"))
            modelo_gb = sum(f.stat().st_size for f in _pesos) / (1024**3)

            # RAM disponível
            ram_gb = 0
            if HAS_PSUTIL:
                ram_gb = psutil.virtual_memory().available / (1024**3)

            # bitsandbytes: quantização 4-bit em GPU NVIDIA (faz o modelo caber na VRAM e roda rápido)
            try:
                import bitsandbytes  # noqa: F401
                HAS_BNB = True
            except Exception:
                HAS_BNB = False
            no_quant = os.environ.get("TAMBAQUI_NO_QUANT", "") in ("1", "true", "yes")
            usar_4bit = False

            if self.device_atual == "auto":
                device = "cpu"
                dtype = torch.float16
                if torch.cuda.is_available():
                    gpu_mem = torch.cuda.get_device_properties(0).total_memory / (1024**3)
                    # 4-bit ocupa ~modelo_gb/4; +1.5GB de folga pro KV cache. Cabe em GPU de 6GB.
                    if HAS_BNB and not no_quant and (modelo_gb * 0.30 + 1.5) < gpu_mem:
                        device = "cuda"; usar_4bit = True
                    elif modelo_gb * 1.2 < gpu_mem:
                        device = "cuda"
                    # senão: fica CPU. Melhor que o split GPU/CPU (device_map=auto) que trava a VRAM.
                elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    device = "mps"
            elif self.device_atual.startswith("cuda"):
                device = self.device_atual
                dtype = torch.float16
                usar_4bit = HAS_BNB and not no_quant
            elif self.device_atual == "mps":
                device = "mps"
                dtype = torch.float16
            else:
                device = "cpu"
                dtype = torch.float16

            # CPU/MPS -> bfloat16: fp16 estoura (no MPS vira LIXO/gibberish; na CPU é emulado/lento).
            # bf16 tem o mesmo alcance do fp32, sem overflow. CUDA mantém fp16 (ou 4-bit).
            if not usar_4bit:
                dtype = torch.float16 if device.startswith("cuda") else torch.bfloat16

            self.device_nome = device + (" (4-bit)" if usar_4bit else "")
            logger.info(f"  Device: {self.device_nome} | Modelo: {modelo_gb:.1f}GB | RAM livre: {ram_gb:.1f}GB")

            # Carregar modelo
            load_kwargs = {"trust_remote_code": trc, "low_cpu_mem_usage": True}

            if usar_4bit:
                from transformers import BitsAndBytesConfig
                load_kwargs["quantization_config"] = BitsAndBytesConfig(
                    load_in_4bit=True, bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch.float16, bnb_4bit_use_double_quant=True)
                load_kwargs["device_map"] = {"": 0}  # modelo inteiro na GPU 0
                logger.info("  ⚡ Quantização 4-bit (nf4) ativada — cabe na VRAM e sobra pro KV cache")
            else:
                load_kwargs["dtype"] = dtype
                # device_map só em CUDA. Em MPS/CPU o modelo é movido pelo .to(device) abaixo —
                # device_map="auto"/"mps" no Mac causava split/erros e saída corrompida.
                if device.startswith("cuda") and modelo_gb > 6:
                    load_kwargs["device_map"] = "auto"
                    logger.info(f"  Modelo grande ({modelo_gb:.1f}GB) - usando device_map=auto")
                elif device.startswith("cuda"):
                    load_kwargs["device_map"] = device

            try:
                self.model = AutoModelForCausalLM.from_pretrained(str(pasta), **load_kwargs)
            except Exception as e1:
                # Fallback: se falhar (MPS buffer, OOM), tentar CPU float16
                logger.warning(f"  Falha no {device}: {e1}")
                logger.info("  Tentando fallback: CPU...")
                # Liberar a VRAM parcial antes de tentar CPU (caso tenha sido OOM)
                try:
                    import gc
                    gc.collect()
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass
                device = "cpu"
                dtype = torch.bfloat16
                self.device_nome = device
                load_kwargs = {"dtype": dtype, "trust_remote_code": trc, "low_cpu_mem_usage": True}
                self.model = AutoModelForCausalLM.from_pretrained(str(pasta), **load_kwargs)

            if not hasattr(self.model, "hf_device_map"):
                self.model = self.model.to(device)
            self.model.eval()

            # --- Otimizações de performance ---

            # 1. Atenção acelerada: transformers usa SDPA (PyTorch 2.x) por padrão quando o modelo
            # suporta. Não usamos mais to_bettertransformer() (deprecated/removido); só logamos o ativo.
            try:
                attn = getattr(self.model.config, "_attn_implementation", "eager")
                logger.info(f"  ⚡ Atenção: {attn}")
            except Exception:
                pass

            # 2. torch.compile — OPT-IN (TAMBAQUI_COMPILE=1). reduce-overhead usa CUDA graphs que, com
            # shapes variáveis de geração e em GPUs Turing (ex: GTX 1660), podem TRAVAR a 1ª request real
            # (o warmup passa porque usa shapes fixos). Default OFF: ganho marginal, risco de hang alto.
            try:
                if (os.environ.get("TAMBAQUI_COMPILE", "") in ("1", "true", "yes")
                        and hasattr(torch, "compile") and device.startswith("cuda") and not usar_4bit):
                    self.model = torch.compile(self.model, mode="reduce-overhead")
                    logger.info("  ⚡ torch.compile ativado (reduce-overhead)")
            except Exception as e:
                logger.warning(f"  torch.compile falhou (não crítico): {e}")

            # 3. KV cache e padding side
            self.tokenizer.padding_side = "left"

            self.modelo_ativo = nome
            # Salvar pra lembrar no modo sob demanda
            cfg = _carregar_config()
            cfg["ultimo_modelo"] = nome
            _salvar_config(cfg)
            n_params = sum(p.numel() for p in self.model.parameters())
            self.n_params = n_params
            logger.info(f"✅ Modelo pronto: {nome} ({n_params/1e9:.1f}B params, {device})")

            # Warmup - gerar tokens dummy pra aquecer caches + compilar
            self._warmup()

            return {"ok": True, "nome": nome, "params": f"{n_params/1e9:.1f}B", "device": device}

        except Exception as e:
            logger.error(f"Erro ao carregar {nome}: {e}")
            self.model = None
            self.tokenizer = None
            return {"erro": str(e)}
        finally:
            self.carregando = False

    def _carregar_gguf(self, nome: str, gguf_path) -> dict:
        """Carrega um modelo GGUF via llama-cpp-python (quantizado)."""
        try:
            from llama_cpp import Llama
        except ImportError:
            return {"erro": "GGUF precisa de llama-cpp-python. Instale: pip install llama-cpp-python"}

        # Libera o modelo transformers anterior (se houver)
        self.model = None
        self.tokenizer = None

        # GPU offload (Metal/CUDA) se o build do llama.cpp suportar e o device pedir
        dev = self.device_atual
        try:
            import torch
            gpu = (dev.startswith("cuda") or dev == "mps" or
                   (dev == "auto" and (torch.cuda.is_available() or
                    (hasattr(torch.backends, "mps") and torch.backends.mps.is_available()))))
        except Exception:
            gpu = dev in ("cuda", "mps")
        n_gpu_layers = -1 if gpu else 0  # -1 = todas as camadas na GPU

        logger.info(f"🧠 Carregando GGUF: {gguf_path.name} (n_gpu_layers={n_gpu_layers})...")
        self.llama = Llama(model_path=str(gguf_path), n_ctx=4096,
                           n_threads=os.cpu_count() or 4, n_gpu_layers=n_gpu_layers,
                           embedding=True, verbose=False)
        self.backend = "llama"
        self.device_nome = "gpu (llama.cpp)" if n_gpu_layers else "cpu (llama.cpp)"
        self.modelo_ativo = nome
        self.n_params = _params_do_nome(gguf_path.name) or _params_do_nome(nome)
        self.warmup_done = True
        cfg = _carregar_config(); cfg["ultimo_modelo"] = nome; _salvar_config(cfg)
        logger.info(f"✅ GGUF pronto: {nome} ({self.device_nome})")
        return {"ok": True, "nome": nome, "backend": "llama", "device": self.device_nome}

    def _llama_completo(self, messages, max_tokens, temperature, top_p, stop=None):
        temperature = 0.0 if self._modelo_pequeno() else self._ajustar_temperatura(temperature)
        kw = dict(messages=messages, max_tokens=max_tokens, temperature=temperature, top_p=top_p)
        if stop:
            kw["stop"] = stop if isinstance(stop, list) else [stop]
        try:
            out = self.llama.create_chat_completion(**kw)
            return (out["choices"][0]["message"].get("content") or "").strip() or None
        except Exception as e:
            logger.error(f"Erro GGUF: {e}")
            return None

    def _llama_stream(self, messages, max_tokens, temperature, top_p, stop=None):
        temperature = 0.0 if self._modelo_pequeno() else self._ajustar_temperatura(temperature)
        kw = dict(messages=messages, max_tokens=max_tokens, temperature=temperature, top_p=top_p, stream=True)
        if stop:
            kw["stop"] = stop if isinstance(stop, list) else [stop]
        try:
            for ch in self.llama.create_chat_completion(**kw):
                delta = ch["choices"][0].get("delta", {}).get("content")
                if delta:
                    yield delta
        except Exception as e:
            logger.error(f"Erro GGUF stream: {e}")
            yield f"\n[Erro: {e}]"

    def embeddings(self, textos):
        """Gera embeddings (só no backend GGUF/llama.cpp, carregado com embedding=True)."""
        if self.backend != "llama" or not self.llama:
            return None
        if isinstance(textos, str):
            textos = [textos]
        try:
            res = self.llama.create_embedding(textos)
            return [d["embedding"] for d in res["data"]]
        except Exception as e:
            logger.error(f"Erro embeddings: {e}")
            return None

    def _warmup(self):
        """Aquece o modelo - compila caches, CUDA graphs, etc."""
        if not self.model or not self.tokenizer:
            return
        logger.info("🔥 Warmup (compilando caches)...")
        try:
            import torch
            # Warmup com tamanhos diferentes pra compilar múltiplos paths
            for prompt in ["def hello():", "# Python function that sorts a list\ndef sort_list(items):"]:
                inputs = self.tokenizer(prompt, return_tensors="pt")
                dev = next(self.model.parameters()).device
                inputs = {k: v.to(dev) for k, v in inputs.items()}
                with torch.inference_mode():
                    self.model.generate(**inputs, max_new_tokens=5, do_sample=False, use_cache=True)
            self.warmup_done = True
            logger.info("🔥 Warmup concluído!")
        except Exception as e:
            logger.warning(f"Warmup falhou (não crítico): {e}")

    def descarregar(self):
        """Libera modelo da RAM."""
        self.model = None
        self.tokenizer = None
        self.llama = None
        self.backend = "transformers"
        self.modelo_ativo = None
        self.warmup_done = False
        import gc
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        logger.info("📦 Modelo descarregado da RAM")

    # --- Geração ---

    def _limpar_cache(self):
        """Libera cache de GPU/CPU após geração."""
        import gc
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
        except Exception:
            pass

    def _resetar_idle_timer(self):
        """Reseta o timer de idle. Se ninguém usar por X min, descarrega."""
        self._ultimo_uso = time.time()

        cfg = _carregar_config()
        idle_min = cfg.get("idle_timeout_min", 0)  # 0 = desativado
        if idle_min <= 0:
            return

        # Cancelar timer anterior
        if hasattr(self, "_idle_timer") and self._idle_timer:
            self._idle_timer.cancel()

        def _idle_check():
            elapsed = time.time() - self._ultimo_uso
            if elapsed >= idle_min * 60 and self.model is not None:
                logger.info(f"💤 Idle {idle_min}min - descarregando modelo pra economizar energia")
                self.descarregar()

        self._idle_timer = threading.Timer(idle_min * 60, _idle_check)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def _auto_carregar(self):
        """Carrega modelo automaticamente (modo ondemand)."""
        if self.pronto():
            return True
        locais = self.listar_locais()
        if not locais:
            return False
        # Usar último modelo ou config salva ou primeiro disponível
        cfg = _carregar_config()
        nome = self.modelo_ativo or cfg.get("ultimo_modelo") or locais[0]["nome"]
        logger.info(f"🔄 Sob demanda - carregando {nome}...")
        self.carregar(nome)
        return self.pronto()

    def gerar(self, messages: list, max_tokens: int = 1024, temperature: float = 0.7,
              top_p: float = 0.9, stream: bool = False, stop: list = None, cancel=None) -> any:
        """Gera resposta. Se stream=True, retorna generator. `stop` = sequências de parada do cliente.
        `cancel` = threading.Event; quando setado, a geração para (disconnect / botão parar)."""
        mode = self.get_load_mode()

        # Sob demanda: carregar antes de gerar
        if mode == "ondemand" and not self.pronto():
            if not self._auto_carregar():
                return None

        if not self.pronto():
            return None

        self._resetar_idle_timer()

        if stream:
            return self._gerar_stream_wrapper(messages, max_tokens, temperature, top_p, mode, stop, cancel)
        else:
            # Lock: o modelo é único e generate() não é thread-safe — serializa requests concorrentes.
            with self._lock:
                resultado = self._gerar_completo(messages, max_tokens, temperature, top_p, stop, cancel)
            self._limpar_cache()
            # Sob demanda: descarregar depois de responder
            if mode == "ondemand":
                logger.info("📦 Sob demanda - descarregando modelo")
                self.descarregar()
            return resultado

    def _gerar_stream_wrapper(self, messages, max_tokens, temperature, top_p, mode=None, stop=None, cancel=None):
        """Wrapper do stream que limpa cache e descarrega se ondemand. Segura o lock por toda a geração."""
        with self._lock:
            for chunk in self._gerar_stream(messages, max_tokens, temperature, top_p, stop, cancel):
                yield chunk
        self._limpar_cache()
        if mode == "ondemand":
            logger.info("📦 Sob demanda - descarregando modelo")
            self.descarregar()

    def _get_sanitizer(self):
        """LogitsProcessorList anti-NaN/inf, criado uma única vez (evita recriar por token/request)."""
        if self._sanitizer is None:
            import torch
            from transformers import LogitsProcessorList, LogitsProcessor

            class _Sanitize(LogitsProcessor):
                def __call__(self, input_ids, scores):
                    return torch.where(torch.isnan(scores) | torch.isinf(scores),
                                       torch.full_like(scores, -1e4), scores)

            self._sanitizer = LogitsProcessorList([_Sanitize()])
        return self._sanitizer

    def _modelo_pequeno(self) -> bool:
        """True para modelos <=3.5B (decidido pelo nº REAL de params, não pelo nome)."""
        return bool(self.n_params) and self.n_params <= 3.5e9

    def _get_gen_kwargs(self, max_tokens: int, temperature: float, top_p: float, stop: list = None, cancel=None) -> dict:
        """Kwargs comuns de geração otimizados."""
        kwargs = {
            "max_new_tokens": max_tokens,
            "pad_token_id": self.tokenizer.pad_token_id or self.tokenizer.eos_token_id,
            "use_cache": True,  # KV cache - acelera geração
            "repetition_penalty": 1.1,
            "logits_processor": self._get_sanitizer(),
        }
        # Modelos pequenos: greedy (do_sample=False) é o anti-alucinação mais eficaz E mais rápido.
        if temperature > 0 and not self._modelo_pequeno():
            kwargs.update({
                "do_sample": True,
                "temperature": max(temperature, 0.01),
                "top_p": top_p,
                "top_k": 40,
            })
        else:
            kwargs["do_sample"] = False
        # Respeitar stop sequences do cliente (aider/cline dependem disso). Precisa do tokenizer.
        if stop:
            kwargs["stop_strings"] = stop if isinstance(stop, list) else [stop]
            kwargs["tokenizer"] = self.tokenizer
        # Cancelamento: para a geração quando o cliente desconecta ou pede stop (libera RAM/compute).
        if cancel is not None:
            from transformers import StoppingCriteria, StoppingCriteriaList

            class _CancelCriteria(StoppingCriteria):
                def __call__(self, input_ids, scores, **kw):
                    return cancel.is_set()

            kwargs["stopping_criteria"] = StoppingCriteriaList([_CancelCriteria()])
        return kwargs

    def _prepare_inputs(self, messages: list):
        """Prepara inputs com device correto."""
        import torch
        text = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self.tokenizer(text, return_tensors="pt", truncation=True, max_length=4096)
        # Mover pro device do modelo
        dev = next(self.model.parameters()).device if hasattr(self.model, "parameters") else "cpu"
        inputs = {k: v.to(dev) for k, v in inputs.items()}
        return inputs, inputs["input_ids"].shape[1]

    def _ajustar_temperatura(self, temperature: float) -> float:
        """Modelos pequenos: temperatura baixa reduz alucinação (decidido pelo nº REAL de params)."""
        if self._modelo_pequeno():
            return min(temperature, 0.3)
        return temperature

    def _validar_resposta(self, resposta: str, messages: list) -> str:
        """Filtra respostas com alucinação óbvia."""
        if not resposta:
            return resposta

        # Pegar última mensagem do user
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break

        # Se era casual e modelo gerou código, remover o código
        if _is_casual(last_user):
            # Remover blocos de código
            limpo = re.sub(r"```[\s\S]*?```", "", resposta).strip()
            if limpo:
                return limpo
            return "Boa tarde! Como posso ajudar com código hoje?"

        # Filtrar repetição excessiva (modelo em loop)
        linhas = resposta.split("\n")
        if len(linhas) > 5:
            unicas = set(linhas)
            if len(unicas) < len(linhas) * 0.3:
                # Mais de 70% repetido - pegar só as únicas
                vistas = set()
                resultado = []
                for l in linhas:
                    if l not in vistas or l.strip() == "":
                        resultado.append(l)
                        vistas.add(l)
                resposta = "\n".join(resultado)

        return resposta

    def _gerar_completo(self, messages: list, max_tokens: int, temperature: float, top_p: float, stop: list = None, cancel=None) -> Optional[str]:
        if self.backend == "llama":
            return self._llama_completo(messages, max_tokens, temperature, top_p, stop)
        import torch

        temperature = self._ajustar_temperatura(temperature)

        try:
            inputs, input_len = self._prepare_inputs(messages)
            gen_kwargs = self._get_gen_kwargs(max_tokens, temperature, top_p, stop, cancel)

            with torch.inference_mode():
                outputs = self.model.generate(**inputs, **gen_kwargs)

            resposta = self.tokenizer.decode(outputs[0][input_len:], skip_special_tokens=True)
            # _validar_resposta (regex destrutivo) removido na Onda 4: corrompia código real.
            # Anti-alucinação agora vem de greedy + prompt curto + cap de tokens (Onda 1).
            return resposta.strip() or None

        except Exception as e:
            logger.error(f"Erro na geração: {e}")
            return None

    def _gerar_stream(self, messages: list, max_tokens: int, temperature: float, top_p: float, stop: list = None, cancel=None) -> Generator:
        """Gera tokens um a um (streaming)."""
        if self.backend == "llama":
            yield from self._llama_stream(messages, max_tokens, temperature, top_p, stop)
            return
        import torch
        from transformers import TextIteratorStreamer

        temperature = self._ajustar_temperatura(temperature)
        gen_timeout = int(os.environ.get("TAMBAQUI_GEN_TIMEOUT", "180"))

        try:
            inputs, _ = self._prepare_inputs(messages)
            # timeout: se generate() travar/morrer, o iterator NÃO fica preso pra sempre — senão o
            # _lock fica segurado e TODAS as próximas requests penduram (bug do TextIteratorStreamer).
            streamer = TextIteratorStreamer(self.tokenizer, skip_prompt=True, skip_special_tokens=True, timeout=gen_timeout)
            gen_kwargs = {**inputs, **self._get_gen_kwargs(max_tokens, temperature, top_p, stop, cancel), "streamer": streamer}

            erro = {}

            def _gen():
                try:
                    with torch.inference_mode():
                        self.model.generate(**gen_kwargs)
                except Exception as e:
                    erro["e"] = e
                    logger.error(f"Erro na geração (thread): {e}")
                    try:
                        streamer.end()  # destrava o iterator quando generate() falha
                    except Exception:
                        pass

            thread = threading.Thread(target=_gen, daemon=True)
            thread.start()

            try:
                for chunk in streamer:
                    if chunk:
                        yield chunk
            except Exception as e:
                # timeout do streamer (geração travou) — reporta em vez de pendurar
                logger.error(f"Streamer interrompido: {e}")
                yield f"\n[Geração interrompida (timeout {gen_timeout}s)]"
            finally:
                # Cliente desconectou/parou OU terminou: garante que a thread de geração pare —
                # sem isso ela continuaria gerando até o fim, alocando RAM/compute à toa.
                if cancel is not None:
                    cancel.set()

            thread.join(timeout=5)
            if erro:
                yield f"\n[Erro: {erro['e']}]"

        except Exception as e:
            logger.error(f"Erro no streaming: {e}")
            yield f"\n[Erro: {e}]"

    def contar_tokens(self, text: str) -> int:
        """Conta tokens de verdade com o tokenizer/modelo (cai pra estimativa se não houver)."""
        if not text:
            return 0
        if self.backend == "llama" and self.llama:
            try:
                return len(self.llama.tokenize(text.encode("utf-8")))
            except Exception:
                pass
        if self.tokenizer:
            try:
                return len(self.tokenizer.encode(text))
            except Exception:
                pass
        return len(text) // 4

    def pronto(self) -> bool:
        if self.backend == "llama":
            return self.llama is not None
        return self.model is not None and self.tokenizer is not None

    def status(self) -> dict:
        return {
            "modelo_ativo": self.modelo_ativo,
            "pronto": self.pronto(),
            "carregando": self.carregando,
            "warmup": self.warmup_done,
            "backend": self.backend,
            "device_config": self.device_atual,
            "device": self.device_nome,
            "downloads": dict(self.progresso_download),
        }


# ============================================================
# SESSÕES
# ============================================================


class Sessao:
    def __init__(self, session_id: str = None, user: str = "default"):
        self.user = user
        self.session_id = session_id or hashlib.sha256(f"{user}:{uuid.uuid4().hex[:8]}".encode()).hexdigest()[:12]
        self.arquivo = SESSOES_DIR / f"{self.session_id}.json"
        self.historico: list = []
        self.titulo = ""
        self.criada_em = datetime.now().isoformat()
        self._carregar()

    def _carregar(self):
        if self.arquivo.exists():
            data = json.loads(self.arquivo.read_text())
            self.historico = data.get("historico", [])
            self.user = data.get("user", self.user)
            self.titulo = data.get("titulo", "")
            self.criada_em = data.get("criada_em", self.criada_em)

    def _salvar(self):
        self.arquivo.write_text(json.dumps({
            "session_id": self.session_id, "user": self.user,
            "criada_em": self.criada_em, "atualizada_em": datetime.now().isoformat(),
            "titulo": self.titulo, "historico": self.historico,
        }, ensure_ascii=False, indent=2))

    def adicionar(self, role: str, conteudo: str):
        self.historico.append({"role": role, "content": conteudo, "timestamp": datetime.now().isoformat()})
        if not self.titulo and role == "user":
            self.titulo = conteudo[:60]
        self._salvar()

    def get_messages(self, max_msgs: int = 20) -> list:
        """Retorna histórico no formato OpenAI messages."""
        msgs = []
        for h in self.historico[-max_msgs:]:
            msgs.append({"role": h["role"], "content": h["content"]})
        return msgs


def listar_sessoes(user: str = None) -> list:
    sessoes = []
    for f in sorted(SESSOES_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text())
            if user and data.get("user") != user:
                continue
            sessoes.append({
                "session_id": data.get("session_id", f.stem),
                "user": data.get("user", ""), "titulo": data.get("titulo", ""),
                "mensagens": len(data.get("historico", [])),
                "atualizada_em": data.get("atualizada_em", ""),
            })
        except Exception:
            continue
    return sessoes[:50]


# ============================================================
# BUSCADORES
# ============================================================


def _extrair_topico(msg: str) -> str:
    stops = {"o","que","é","oque","qual","como","quem","onde","por","porque","um","uma",
             "de","da","do","em","no","na","para","com","se","eu","você","voce","me","te",
             "fale","sobre","explique","a","os","as","fazer","faz","posso","pode","crie","gere"}
    palavras = re.findall(r"\w+", msg.lower())
    return " ".join(p for p in palavras if p not in stops and len(p) > 1) or msg


def _detectar_lang(msg: str) -> str:
    m = msg.lower()
    mapa = {"python":"python","js":"javascript","javascript":"javascript","typescript":"typescript",
            "rust":"rust","go":"go","java":"java","sql":"sql","bash":"bash","html":"html","css":"css",
            "php":"php","ruby":"ruby","c++":"cpp","docker":"docker"}
    for k, v in mapa.items():
        if k in m:
            return v
    return ""


_search_session = requests.Session()
_search_session.headers.update(HTTP_HEADERS)


def _buscar_wikipedia(topico, lang):
    try:
        busca = f"{topico} {'programação' if not lang else lang + ' software'}"
        resp = _search_session.get("https://pt.wikipedia.org/w/api.php", params={
            "action": "query", "list": "search", "srsearch": busca,
            "srlimit": 2, "format": "json", "utf8": 1,
        }, timeout=3)
        resultados = resp.json().get("query", {}).get("search", [])
        if resultados:
            titulo = resultados[0]["title"]
            resp2 = _search_session.get("https://pt.wikipedia.org/w/api.php", params={
                "action": "query", "titles": titulo, "prop": "extracts",
                "explaintext": True, "exintro": True, "format": "json", "utf8": 1,
            }, timeout=3)
            for page in resp2.json().get("query", {}).get("pages", {}).values():
                if page.get("extract"):
                    return f"Wikipedia ({titulo}):\n{page['extract'][:2000]}\n\n", "Wikipedia"
    except Exception:
        pass
    return "", ""


def _buscar_stackoverflow(topico, lang):
    try:
        tag = f"[{lang}]" if lang else ""
        resp = _search_session.get("https://api.stackexchange.com/2.3/search/excerpts", params={
            "order": "desc", "sort": "relevance", "q": f"{topico} {tag}",
            "site": "stackoverflow", "pagesize": 3,
        }, timeout=3)
        ctx = ""
        for item in resp.json().get("items", [])[:3]:
            titulo = BeautifulSoup(item.get("title", ""), "html.parser").get_text()
            corpo = BeautifulSoup(item.get("excerpt", ""), "html.parser").get_text()
            ctx += f"StackOverflow: {titulo}\n{corpo[:300]}\n\n"
        if ctx:
            return ctx, "StackOverflow"
    except Exception:
        pass
    return "", ""


def _buscar_ddg(topico, lang):
    try:
        q = f"{topico} {lang} code" if lang else f"{topico} programming"
        resp = _search_session.get("https://lite.duckduckgo.com/lite/", params={"q": q}, timeout=4)
        soup = BeautifulSoup(resp.text, "html.parser")
        ctx = ""
        for link in soup.select("a.result-link")[:5]:
            ctx += f"Web: {link.get_text(strip=True)}\n"
        if ctx:
            return ctx, "DuckDuckGo"
    except Exception:
        pass
    return "", ""


def buscar_web(query: str) -> dict:
    """Busca em paralelo: Wikipedia + StackOverflow + DuckDuckGo."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    topico = _extrair_topico(query)
    lang = _detectar_lang(query)
    contexto = ""
    fontes = []

    # Buscar tudo em paralelo (3x mais rápido)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(_buscar_wikipedia, topico, lang): "wiki",
            pool.submit(_buscar_stackoverflow, topico, lang): "so",
            pool.submit(_buscar_ddg, topico, lang): "ddg",
        }
        for future in as_completed(futures, timeout=6):
            try:
                ctx, fonte = future.result()
                if ctx:
                    contexto += ctx
                    fontes.append(fonte)
            except Exception:
                pass

    return {"contexto": contexto[:5000], "fontes": fontes}


# ============================================================
# FASTAPI - API OpenAI-compatível + Admin
# ============================================================

from fastapi import FastAPI, Request, Response, Cookie
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app):
    # Startup: decide o carregamento do modelo conforme o modo configurado.
    locais = manager.listar_locais()
    mode = manager.get_load_mode()
    if locais and mode == "preload":
        logger.info(f"🚀 Pre-load ativado. Carregando {locais[0]['nome']}...")
        threading.Thread(target=lambda: manager.carregar(locais[0]["nome"]), daemon=True).start()
    elif locais and mode == "ondemand":
        logger.info("⚡ Modo sob demanda. Modelo carrega quando chegar request.")
    elif locais:
        logger.info(f"📦 {len(locais)} modelo(s). Carregue pelo /admin.")
    yield


app = FastAPI(title="Tambaqui", version="2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

manager = ModelManager()

# Backpressure: limita a profundidade da fila de geração. O modelo já é serializado pelo _lock;
# isto evita que requests se acumulem sem limite e devolve 429 quando a fila enche.
MAX_QUEUE = int(os.environ.get("TAMBAQUI_MAX_QUEUE", "16"))


class _Backpressure:
    def __init__(self, max_queue: int):
        self.max_queue = max_queue
        self.inflight = 0
        self._lock = threading.Lock()

    def try_enter(self) -> bool:
        with self._lock:
            if self.inflight >= self.max_queue:
                return False
            self.inflight += 1
            return True

    def leave(self):
        with self._lock:
            self.inflight = max(0, self.inflight - 1)


_bp = _Backpressure(MAX_QUEUE)


def _resp_429():
    return JSONResponse(
        status_code=429,
        content={"error": {"message": "Servidor ocupado (fila cheia). Tente de novo em instantes.",
                           "type": "rate_limit_error", "code": "queue_full"}},
        headers={"Retry-After": "5"},
    )


# Rate limiting opcional por API key/IP (requisições/minuto). 0 = desligado.
RATE_LIMIT = int(os.environ.get("TAMBAQUI_RATE_LIMIT", "0"))


class RateLimiter:
    def __init__(self, per_min: int):
        self.per_min = per_min
        self._hits = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.per_min <= 0:
            return True
        now = time.time()
        with self._lock:
            q = self._hits.setdefault(key, [])
            cutoff = now - 60
            while q and q[0] < cutoff:
                q.pop(0)
            if len(q) >= self.per_min:
                return False
            q.append(now)
            return True


_rl = RateLimiter(RATE_LIMIT)


def _rate_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return "k:" + auth[7:][:16]
    return "ip:" + (request.client.host if request.client else "?")


# (startup migrado pro lifespan handler acima — antes era @app.on_event("startup"))


# --- Auth ---

class LoginRequest(BaseModel):
    username: str
    senha: str

class CriarUserRequest(BaseModel):
    username: str
    senha: str
    admin: bool = False

class TrocarSenhaRequest(BaseModel):
    username: str
    nova_senha: str


@app.post("/api/auth/login")
async def api_login(req: LoginRequest, response: Response):
    token = user_login(req.username, req.senha)
    if not token:
        return JSONResponse(status_code=401, content={"erro": "Usuário ou senha inválidos"})
    response.set_cookie("tambaqui_token", token, httponly=True, max_age=86400*7, samesite="lax")
    return {"ok": True, "token": token}


@app.post("/api/auth/logout")
async def api_logout(request: Request, response: Response):
    token = request.cookies.get("tambaqui_token")
    if token:
        _tokens.delete(token)
    response.delete_cookie("tambaqui_token")
    return {"ok": True}


@app.get("/api/auth/me")
async def api_me(request: Request):
    user = _get_user_from_request(request)
    if not user:
        return JSONResponse(status_code=401, content={"erro": "Não autenticado"})
    return user


@app.get("/api/auth/users")
async def api_users(request: Request):
    user = _get_user_from_request(request)
    if not user or not user.get("admin"):
        return JSONResponse(status_code=403, content={"erro": "Apenas admin"})
    return {"users": user_listar()}


@app.post("/api/auth/users")
async def api_criar_user(req: CriarUserRequest, request: Request):
    user = _get_user_from_request(request)
    if not user or not user.get("admin"):
        return JSONResponse(status_code=403, content={"erro": "Apenas admin"})
    return user_criar(req.username, req.senha, req.admin)


@app.post("/api/auth/trocar-senha")
async def api_trocar_senha(req: TrocarSenhaRequest, request: Request):
    user = _get_user_from_request(request)
    if not user:
        return JSONResponse(status_code=401, content={"erro": "Não autenticado"})
    # Admin pode trocar de qualquer um, user normal só a própria
    if not user.get("admin") and req.username != user["username"]:
        return JSONResponse(status_code=403, content={"erro": "Sem permissão"})
    return user_trocar_senha(req.username, req.nova_senha)


@app.delete("/api/auth/users/{username}")
async def api_deletar_user(username: str, request: Request):
    user = _get_user_from_request(request)
    if not user or not user.get("admin"):
        return JSONResponse(status_code=403, content={"erro": "Apenas admin"})
    if username == user["username"]:
        return JSONResponse(status_code=400, content={"erro": "Não pode deletar a si mesmo"})
    return user_deletar(username)


@app.get("/api/auth/api-key")
async def api_get_key(request: Request):
    """Retorna a API key do user logado + config de conexão com URL real."""
    user = _get_user_from_request(request)
    if not user:
        return JSONResponse(status_code=401, content={"erro": "Não autenticado"})
    key = user_get_api_key(user["username"])
    modelo = manager.modelo_ativo or ""
    # URL real: pegar do request (o que o browser usou pra chegar aqui)
    host = request.headers.get("host", f"localhost:{PORT}")
    scheme = request.headers.get("x-forwarded-proto", "http")
    base = f"{scheme}://{host}"
    return {
        "api_key": key,
        "username": user["username"],
        "base_url": f"{base}/v1",
        "model": modelo,
    }


@app.post("/api/auth/api-key/regenerar")
async def api_regen_key(request: Request):
    user = _get_user_from_request(request)
    if not user:
        return JSONResponse(status_code=401, content={"erro": "Não autenticado"})
    return user_regenerar_key(user["username"])


# --- OpenAI-Compatible API ---

class ChatMessage(BaseModel):
    role: str
    content: str = ""

class ChatRequest(BaseModel):
    model: str = ""
    messages: List[ChatMessage]
    temperature: float = 0.7
    max_tokens: Optional[int] = 2048
    top_p: float = 0.9
    stream: bool = False
    stop: Optional[List[str]] = None
    n: int = 1
    presence_penalty: float = 0
    frequency_penalty: float = 0

    model_config = ConfigDict(extra="allow")  # aceitar campos extras sem erro

class CompletionRequest(BaseModel):
    model: str = ""
    prompt: str = ""
    max_tokens: int = 256
    temperature: float = 0.7
    stream: bool = False
    stop: Optional[List[str]] = None

    model_config = ConfigDict(extra="allow")


class EmbeddingRequest(BaseModel):
    model: str = ""
    input: Union[str, List[str]]

    model_config = ConfigDict(extra="allow")


# ============================================================
# API LOGS
# ============================================================

API_LOGS: list = []  # ring buffer de logs
API_LOGS_MAX = 200


def _log_api(tipo: str, data: dict):
    """Registra evento na API."""
    entry = {"timestamp": datetime.now().isoformat(), "tipo": tipo, **data}
    API_LOGS.append(entry)
    if len(API_LOGS) > API_LOGS_MAX:
        API_LOGS.pop(0)
    logger.info(f"[API] {tipo}: {json.dumps({k: str(v)[:100] for k, v in data.items()}, ensure_ascii=False)}")


class Atividade:
    """Rastreia gerações ao vivo: o que está fazendo, % processado (tokens/max), tempo, erro."""

    def __init__(self, maxn: int = 25):
        self.atual = {}      # id -> registro em andamento
        self.recentes = []   # últimas N concluídas/erradas
        self.maxn = maxn
        self._lock = threading.Lock()

    def iniciar(self, id, **kw):
        with self._lock:
            self.atual[id] = {"id": id, "estado": "iniciando", "tokens": 0, "percent": 0,
                              "inicio": time.time(), "elapsed": 0.0, **kw}

    def set(self, id, **kw):
        with self._lock:
            a = self.atual.get(id)
            if a:
                a.update(kw)
                a["elapsed"] = round(time.time() - a["inicio"], 1)

    def tick(self, id, max_tokens: int):
        with self._lock:
            a = self.atual.get(id)
            if a:
                a["tokens"] += 1
                a["estado"] = "gerando"
                a["percent"] = min(99, int(a["tokens"] / max(max_tokens, 1) * 100))
                a["elapsed"] = round(time.time() - a["inicio"], 1)

    def erro(self, id, msg):
        with self._lock:
            a = self.atual.get(id)
            if a:
                a["estado"] = "erro"
                a["erro"] = str(msg)[:300]

    def concluir(self, id):
        with self._lock:
            a = self.atual.pop(id, None)
            if not a:
                return
            if a.get("estado") not in ("erro", "cancelado"):
                a["estado"] = "concluido"
                a["percent"] = 100
            a["elapsed"] = round(time.time() - a["inicio"], 1)
            a["fim"] = datetime.now().isoformat()
            self.recentes.insert(0, a)
            self.recentes = self.recentes[:self.maxn]

    def snapshot(self):
        with self._lock:
            return {"atual": list(self.atual.values()), "recentes": list(self.recentes)}


_atividade = Atividade()

# Cancelamento de geração: id -> threading.Event (acionado pelo botão "parar" ou por disconnect).
_cancels = {}
_cancels_lock = threading.Lock()


def _novo_cancel(id):
    ev = threading.Event()
    with _cancels_lock:
        _cancels[id] = ev
    return ev


def _disparar_cancel(id) -> bool:
    with _cancels_lock:
        ev = _cancels.get(id)
    if ev:
        ev.set()
        return True
    return False


def _remover_cancel(id):
    with _cancels_lock:
        _cancels.pop(id, None)


def _contar_tokens(text: str) -> int:
    """Estimativa simples de tokens (~4 chars por token)."""
    return len(text) // 4


def _detectar_cli(messages: list) -> str:
    """Detecta qual CLI está chamando baseado no system prompt."""
    if not messages:
        return ""
    sys_msg = ""
    for m in messages:
        if m.get("role") == "system":
            sys_msg += m.get("content", "")
    s = sys_msg.lower()
    if "search/replace" in s or "aider" in s:
        return "aider"
    if "hermes" in s:
        return "hermes"
    if "cline" in s or "roo" in s:
        return "cline"
    if "continue" in s:
        return "continue"
    return ""


def _is_casual(text: str) -> bool:
    """Detecta se a mensagem é conversa casual (não pedido de código)."""
    t = text.lower().strip().rstrip("?!.")
    palavras = t.split()
    if len(palavras) > 6:
        return False
    casual = {"oi", "ola", "olá", "boa", "bom", "tarde", "noite", "dia", "manhã",
              "tudo", "bem", "beleza", "eai", "fala", "obrigado", "valeu", "thanks"}
    return all(p in casual for p in palavras)


def _reforcar_system_prompt(messages: list, cli: str) -> list:
    """Injeta instruções extras no system prompt pra ajudar modelos pequenos."""
    # Pegar a última mensagem do user
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = m.get("content", "")
            break

    # Se é conversa casual, injetar instrução forte
    if _is_casual(last_user):
        reforco = (
            "\n\nATENÇÃO: A mensagem do usuário é um CUMPRIMENTO casual. "
            "Responda APENAS com texto normal tipo 'Boa tarde! Como posso ajudar com código hoje?'. "
            "NÃO gere código. NÃO crie funções. NÃO mostre exemplos. Apenas cumprimente de volta."
        )
        for m in messages:
            if m.get("role") == "system":
                m["content"] += reforco
                break

    # Se é Aider, reforçar formato
    if cli == "aider":
        reforco = (
            "\n\nIMPORTANTE: Se o usuário fizer um cumprimento casual (oi, boa tarde, etc), "
            "responda APENAS com texto, sem código e sem blocos de código."
        )
        for m in messages:
            if m.get("role") == "system":
                m["content"] += reforco
                break

    return messages


@app.get("/v1/models")
@app.get("/v1/models/{model_id}")
async def list_models(model_id: str = None):
    """Lista modelos no formato OpenAI."""
    modelos = manager.listar_locais()
    data = []
    for m in modelos:
        obj = {
            "id": m["nome"],
            "object": "model",
            "created": int(time.time()),
            "owned_by": "tambaqui",
            "permission": [],
            "root": m["nome"],
            "parent": None,
        }
        if model_id and m["nome"] == model_id:
            return obj
        data.append(obj)
    if model_id:
        return JSONResponse(status_code=404, content={"error": {"message": f"Model '{model_id}' not found", "type": "invalid_request_error"}})
    return {"object": "list", "data": data}


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest, request: Request):
    """Endpoint principal - compatível com OpenAI API."""
    if _auth_ativo() and not _get_user_from_request(request):
        return JSONResponse(status_code=401, content={"error": {"message": "Invalid API key. Use Authorization: Bearer tb-...", "type": "invalid_api_key", "code": "invalid_api_key"}})
    if not _rl.allow(_rate_key(request)):
        return _resp_429()
    if not manager.pronto():
        # Sob demanda: tentar carregar automaticamente
        if manager.get_load_mode() == "ondemand":
            if not manager._auto_carregar():
                return JSONResponse(status_code=503, content={"error": {"message": "No models available to load", "type": "server_error"}})
        else:
            return JSONResponse(status_code=503, content={"error": {"message": "No model loaded. Open /admin to download and load a model.", "type": "server_error"}})

    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    max_tokens = req.max_tokens or 1024

    # Pegar última msg do user pra log
    last_user_msg = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user_msg = m.get("content", "")[:200]
            break

    user = _get_user_from_request(request)
    username = user["username"] if user else "anon"

    # Detectar CLI e reforçar instruções pro modelo
    cli = _detectar_cli(messages)
    is_casual_msg = _is_casual(last_user_msg)
    messages = _reforcar_system_prompt(messages, cli)

    # Injeta o system prompt do Tambaqui quando o cliente não manda um (CLIs via /v1 não mandam),
    # adaptado ao tamanho do modelo. É o que faz o anti-alucinação finalmente atuar no caminho da API.
    if not any(m.get("role") == "system" for m in messages):
        messages.insert(0, {"role": "system", "content": _get_system_prompt(manager.n_params)})
    # Cumprimento casual não precisa de 1024 tokens.
    if is_casual_msg:
        max_tokens = min(max_tokens, 128)

    _log_api("request", {
        "user": username, "cli": cli or "web", "modelo": manager.modelo_ativo,
        "mensagem": last_user_msg, "casual": is_casual_msg,
        "msgs": len(messages), "stream": req.stream, "temp": req.temperature,
    })

    # Se modo "busca", injetar contexto web na última mensagem do user
    if manager.get_api_mode() == "busca" and messages:
        last_user = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i]["role"] == "user":
                last_user = i
                break
        if last_user is not None:
            pesquisa = buscar_web(messages[last_user]["content"])
            if pesquisa["contexto"]:
                messages[last_user]["content"] = f"Contexto da pesquisa:\n{pesquisa['contexto'][:2500]}\n\n---\n{messages[last_user]['content']}"

    t0 = time.time()

    if req.stream:
        if not _bp.try_enter():
            return _resp_429()
        return StreamingResponse(_stream_response(messages, req, max_tokens), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # Resposta completa
    if not _bp.try_enter():
        return _resp_429()
    prompt_text = " ".join(m["content"] for m in messages)
    _aid = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    _atividade.iniciar(_aid, modelo=manager.modelo_ativo or "", max_tokens=max_tokens,
                       fonte="API", mensagem=last_user_msg[:80], estado="gerando")
    _cancel = _novo_cancel(_aid)
    try:
        # run_in_threadpool: generate() é bloqueante; sem isso travaria o event loop inteiro
        # (health, /admin, outros usuários congelavam durante cada geração).
        resposta = await run_in_threadpool(
            manager.gerar, messages, max_tokens, req.temperature, req.top_p, False, req.stop, _cancel
        ) or ""
        _atividade.set(_aid, tokens=manager.contar_tokens(resposta), percent=100)
    except Exception as _e:
        _atividade.erro(_aid, _e)
        raise
    finally:
        _remover_cancel(_aid)
        _atividade.concluir(_aid)
        _bp.leave()

    elapsed = round(time.time() - t0, 1)
    prompt_tokens = manager.contar_tokens(prompt_text)
    completion_tokens = manager.contar_tokens(resposta)

    _log_api("response", {
        "user": username, "tempo": f"{elapsed}s",
        "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
        "resposta": resposta[:200],
    })

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": manager.modelo_ativo or req.model or "",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": resposta},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


@app.post("/v1/completions")
async def completions(req: CompletionRequest, request: Request):
    """Legacy completions endpoint."""
    if _auth_ativo() and not _get_user_from_request(request):
        return JSONResponse(status_code=401, content={"error": {"message": "Invalid API key", "type": "invalid_api_key"}})
    if not _rl.allow(_rate_key(request)):
        return _resp_429()
    if not manager.pronto():
        if manager.get_load_mode() == "ondemand":
            if not manager._auto_carregar():
                return JSONResponse(status_code=503, content={"error": {"message": "No models available", "type": "server_error"}})
        else:
            return JSONResponse(status_code=503, content={"error": {"message": "No model loaded", "type": "server_error"}})

    messages = [{"role": "user", "content": req.prompt}]
    if not _bp.try_enter():
        return _resp_429()
    try:
        resposta = await run_in_threadpool(
            manager.gerar, messages, req.max_tokens, req.temperature, 0.9, False, req.stop
        ) or ""
    finally:
        _bp.leave()

    p_tok = manager.contar_tokens(req.prompt)
    c_tok = manager.contar_tokens(resposta)
    return {
        "id": f"cmpl-{uuid.uuid4().hex[:8]}",
        "object": "text_completion",
        "created": int(time.time()),
        "model": manager.modelo_ativo or "",
        "choices": [{"text": resposta, "index": 0, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": p_tok, "completion_tokens": c_tok, "total_tokens": p_tok + c_tok},
    }


@app.post("/v1/embeddings")
async def embeddings(req: EmbeddingRequest, request: Request):
    """Embeddings compatíveis com OpenAI — requer um modelo GGUF (llama.cpp) carregado."""
    if _auth_ativo() and not _get_user_from_request(request):
        return JSONResponse(status_code=401, content={"error": {"message": "Invalid API key", "type": "invalid_api_key"}})
    if not _rl.allow(_rate_key(request)):
        return _resp_429()
    if manager.backend != "llama" or not manager.pronto():
        return JSONResponse(status_code=501, content={"error": {
            "message": "Embeddings exigem um modelo GGUF (llama.cpp) carregado. Baixe um .gguf pelo Admin.",
            "type": "not_implemented"}})
    if not _bp.try_enter():
        return _resp_429()
    try:
        vecs = await run_in_threadpool(manager.embeddings, req.input)
    finally:
        _bp.leave()
    if vecs is None:
        return JSONResponse(status_code=500, content={"error": {"message": "Falha ao gerar embeddings", "type": "server_error"}})
    data = [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vecs)]
    texto = req.input if isinstance(req.input, str) else " ".join(req.input)
    toks = manager.contar_tokens(texto)
    return {"object": "list", "data": data, "model": manager.modelo_ativo or req.model or "",
            "usage": {"prompt_tokens": toks, "total_tokens": toks}}


def _stream_response(messages: list, req: ChatRequest, max_tokens: int = 1024):
    """Gera SSE no formato OpenAI streaming - compatível com todos os CLIs."""
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    model = manager.modelo_ativo or req.model or ""
    created = int(time.time())
    _msg = next((m.get("content", "")[:80] for m in reversed(messages) if m.get("role") == "user"), "")
    _atividade.iniciar(chat_id, modelo=model, max_tokens=max_tokens, fonte="API", mensagem=_msg, estado="gerando")
    cancel = _novo_cancel(chat_id)

    try:
        # Primeiro chunk: role
        yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"

        for chunk in manager.gerar(messages, max_tokens, req.temperature, req.top_p, stream=True, stop=req.stop, cancel=cancel):
            if isinstance(chunk, str) and (chunk.startswith("[Erro") or chunk.startswith("[Geração interrompida")):
                _atividade.erro(chat_id, chunk)
            else:
                _atividade.tick(chat_id, max_tokens)
            data = {
                "id": chat_id, "object": "chat.completion.chunk",
                "created": created, "model": model,
                "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(data)}\n\n"

        # Final
        yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
        yield "data: [DONE]\n\n"
    finally:
        _remover_cancel(chat_id)
        _atividade.concluir(chat_id)
        _bp.leave()


# --- Tambaqui API (chat com busca + sessão) ---

class TambaquiChatRequest(BaseModel):
    mensagem: str
    session_id: Optional[str] = None
    user: str = "default"
    buscar_web: bool = True


@app.post("/api/chat")
async def tambaqui_chat(req: TambaquiChatRequest):
    """Chat Tambaqui com streaming: busca web → stream do modelo token a token."""
    if not _bp.try_enter():
        return _resp_429()
    sessao = Sessao(req.session_id, req.user)
    sessao.adicionar("user", req.mensagem)
    _aid = "chat-" + sessao.session_id
    _atividade.iniciar(_aid, modelo=manager.modelo_ativo or "", max_tokens=1024, fonte="Chat web",
                       mensagem=req.mensagem[:80], estado="buscando_web" if req.buscar_web else "gerando")
    _cancel = _novo_cancel(_aid)

    def _stream():
        try:
            # 1. Buscar contexto web
            contexto = ""
            fontes = []
            if req.buscar_web:
                pesquisa = buscar_web(req.mensagem)
                contexto = pesquisa["contexto"]
                fontes = pesquisa["fontes"]
            _atividade.set(_aid, estado="gerando")

            # Enviar fontes + session_id primeiro
            yield f"data: {json.dumps({'type': 'meta', 'fontes': fontes, 'session_id': sessao.session_id})}\n\n"

            # 2. Montar messages (prompt adaptado ao tamanho do modelo)
            messages = [{"role": "system", "content": _get_system_prompt(manager.n_params)}]
            messages.extend(sessao.get_messages(max_msgs=10))

            if contexto and messages:
                last = messages[-1]
                if last["role"] == "user":
                    last["content"] = f"Contexto da pesquisa:\n{contexto[:2500]}\n\n---\n{last['content']}"

            # 3. Stream do modelo
            full_response = ""
            if manager.pronto():
                try:
                    for chunk in manager.gerar(messages, max_tokens=1024, stream=True, cancel=_cancel):
                        if isinstance(chunk, str) and (chunk.startswith("[Erro") or chunk.startswith("[Geração interrompida")):
                            _atividade.erro(_aid, chunk)
                        else:
                            _atividade.tick(_aid, 1024)
                        full_response += chunk
                        yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
                except Exception as e:
                    _atividade.erro(_aid, e)
                    logger.error(f"Stream erro: {e}")

            # 4. Fallback se modelo não respondeu
            if not full_response and contexto:
                full_response = contexto[:3000]
                yield f"data: {json.dumps({'type': 'chunk', 'content': full_response})}\n\n"
            elif not full_response:
                full_response = "Modelo não carregado. Acesse /admin para baixar e carregar."
                yield f"data: {json.dumps({'type': 'chunk', 'content': full_response})}\n\n"

            # 5. Salvar e finalizar
            sessao.adicionar("assistant", full_response)
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        finally:
            _remover_cancel(_aid)
            _atividade.concluir(_aid)
            _bp.leave()

    return StreamingResponse(_stream(), media_type="text/event-stream")


# --- Admin API ---

@app.get("/api/status")
async def api_status():
    return {**manager.status(), **get_system_info()}

@app.get("/api/modelos")
async def api_modelos():
    return {"locais": manager.listar_locais(), "catalogo": manager.listar_catalogo()}

@app.post("/api/modelos/baixar")
async def api_baixar(nome: str):
    return manager.baixar(nome)

@app.get("/api/hub/buscar")
async def api_hub_buscar(q: str = "", limit: int = 20):
    """Busca modelos de geração de texto no HuggingFace Hub pra pessoa escolher."""
    limit = max(1, min(limit, 50))
    return {"resultados": await run_in_threadpool(manager.buscar_hub, q, limit)}

@app.get("/api/hub/gguf")
async def api_hub_gguf(repo: str):
    """Lista as quantizações .gguf de um repo GGUF."""
    return {"arquivos": await run_in_threadpool(manager.listar_gguf_repo, repo)}

@app.post("/api/modelos/baixar-gguf")
async def api_baixar_gguf(repo: str, arquivo: str):
    """Baixa uma quantização específica (.gguf) de um repo."""
    return manager.baixar_gguf(repo, arquivo)

@app.get("/api/trust-remote-code")
async def api_get_trc():
    return {"trust_remote_code": manager.get_trust_remote_code()}

@app.post("/api/trust-remote-code")
async def api_set_trc(val: bool):
    return manager.set_trust_remote_code(val)

@app.post("/api/modelos/carregar")
async def api_carregar(nome: str):
    def _load():
        manager.carregar(nome)
    threading.Thread(target=_load, daemon=True).start()
    return {"status": f"Carregando {nome}..."}

@app.post("/api/modelos/descarregar")
async def api_descarregar():
    manager.descarregar()
    return {"ok": True}

@app.delete("/api/modelos/{nome}")
async def api_deletar_modelo(nome: str):
    return manager.deletar(nome)

@app.get("/api/logs")
async def api_logs(limit: int = 50):
    return {"logs": API_LOGS[-limit:]}

@app.get("/api/atividade")
async def api_atividade():
    """Gerações ao vivo: o que está processando, % (tokens/max), tempo, erro."""
    return _atividade.snapshot()

@app.post("/api/atividade/{id}/parar")
async def api_parar(id: str):
    """Para a geração de um pedido específico (libera RAM/compute imediatamente)."""
    ok = _disparar_cancel(id)
    if ok:
        _atividade.set(id, estado="cancelado")
    return {"ok": ok}

@app.get("/api/load-mode")
async def api_get_load_mode():
    return {"load_mode": manager.get_load_mode()}

@app.post("/api/load-mode")
async def api_set_load_mode(mode: str):
    return manager.set_load_mode(mode)

@app.get("/api/idle")
async def api_get_idle():
    return {"idle_timeout_min": manager.get_idle_timeout()}

@app.post("/api/idle")
async def api_set_idle(minutes: int):
    return manager.set_idle_timeout(minutes)

@app.get("/api/mode")
async def api_get_mode():
    return {"mode": manager.get_api_mode()}

@app.post("/api/mode")
async def api_set_mode(mode: str):
    return manager.set_api_mode(mode)

@app.get("/api/devices")
async def api_devices():
    return {"devices": manager.detectar_devices(), "atual": manager.device_atual}

@app.post("/api/devices")
async def api_set_device(device: str):
    return manager.set_device(device)

@app.get("/api/sessoes")
async def api_sessoes(user: str = None):
    return {"sessoes": listar_sessoes(user)}

@app.get("/api/sessoes/{sid}")
async def api_sessao_detalhe(sid: str):
    sessao = Sessao(sid)
    return {
        "session_id": sessao.session_id, "titulo": sessao.titulo,
        "historico": [{"role": h["role"], "content": h["content"]} for h in sessao.historico],
    }

@app.delete("/api/sessoes/{sid}")
async def api_deletar_sessao(sid: str):
    f = SESSOES_DIR / f"{sid}.json"
    if f.exists():
        f.unlink()
        return {"ok": True}
    return {"erro": "Não encontrada"}


STATIC_DIR = Path(__file__).parent / "static"

def _read_html(name: str) -> str:
    return (STATIC_DIR / name).read_text()

@app.get("/login")
async def login_page():
    if not _auth_ativo():
        return RedirectResponse("/")
    return HTMLResponse(_read_html("login.html"))

@app.get("/")
async def root(request: Request):
    if _auth_ativo() and not _get_user_from_request(request):
        return RedirectResponse("/login")
    return HTMLResponse(_read_html("chat.html"))

@app.get("/admin")
async def admin(request: Request):
    if _auth_ativo():
        user = _get_user_from_request(request)
        if not user:
            return RedirectResponse("/login")
        if not user.get("admin"):
            return HTMLResponse("<h1>Acesso negado</h1><a href='/'>Voltar</a>", status_code=403)
    return HTMLResponse(_read_html("admin.html"))


# HTML movido para static/login.html, static/chat.html, static/admin.html

# ============================================================
# CLI
# ============================================================

def cli_chat():
    print()
    print("=" * 55)
    print("  🐟 Tambaqui CLI")
    print("=" * 55)
    print()
    print("  /sair  /nova  /status  /modelos")
    print()

    # Carregar modelo
    locais = manager.listar_locais()
    if locais:
        if not manager.pronto():
            print(f"  Carregando {locais[0]['nome']}...")
            manager.carregar(locais[0]["nome"])
    else:
        print("  Nenhum modelo. Rode o server (python3 app.py) e acesse /admin")
        return

    user = os.environ.get("USER", "cli")
    sessao = Sessao(user=user)
    print(f"  Modelo: {manager.modelo_ativo}")
    print(f"  Sessão: {sessao.session_id}")
    print()

    while True:
        try:
            msg = input("\033[36mVocê>\033[0m ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nAté logo!")
            break

        if not msg:
            continue
        if msg == "/sair":
            break
        elif msg == "/nova":
            sessao = Sessao(user=user)
            print(f"  Nova sessão: {sessao.session_id}")
            continue
        elif msg == "/status":
            s = manager.status()
            si = get_system_info()
            print(f"  Modelo: {s['modelo_ativo'] or 'nenhum'} | Pronto: {s['pronto']} | Warmup: {s['warmup']}")
            print(f"  CPU: {si.get('cpu_percent',0)}% | RAM: {si.get('ram_usada_gb',0)}/{si.get('ram_total_gb',0)}GB | Processo: {si.get('proc_ram_mb',0)}MB")
            continue
        elif msg == "/modelos":
            for m in manager.listar_locais():
                print(f"  {'●' if m['ativo'] else '○'} {m['nome']} ({m['tamanho_gb']}GB)")
            continue

        # Gerar
        sessao.adicionar("user", msg)
        messages = [{"role": "system", "content": _get_system_prompt(manager.n_params)}]
        messages.extend(sessao.get_messages(max_msgs=10))

        print()
        print(f"\033[32m🐟 Tambaqui>\033[0m")

        if manager.pronto():
            # Streaming no terminal
            full = ""
            for chunk in manager.gerar(messages, stream=True):
                print(chunk, end="", flush=True)
                full += chunk
            print("\n")
            sessao.adicionar("assistant", full)
        else:
            print("Modelo não carregado.\n")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "chat":
        cli_chat()

    elif len(sys.argv) > 1 and sys.argv[1] == "user":
        # python3 app.py user criar nome senha [--admin]
        # python3 app.py user listar
        # python3 app.py user senha nome nova_senha
        # python3 app.py user deletar nome
        if len(sys.argv) < 3:
            print("Uso:")
            print("  python3 app.py user criar <nome> <senha> [--admin]")
            print("  python3 app.py user listar")
            print("  python3 app.py user senha <nome> <nova_senha>")
            print("  python3 app.py user deletar <nome>")
            sys.exit(1)

        cmd = sys.argv[2]

        if cmd == "criar":
            if len(sys.argv) < 5:
                print("Uso: python3 app.py user criar <nome> <senha> [--admin]")
                sys.exit(1)
            nome = sys.argv[3]
            senha = sys.argv[4]
            is_admin = "--admin" in sys.argv
            r = user_criar(nome, senha, is_admin)
            if r.get("ok"):
                role = "admin" if is_admin else "user"
                print(f"✅ Usuário '{nome}' criado ({role})")
            else:
                print(f"❌ {r.get('erro')}")

        elif cmd == "listar":
            users = user_listar()
            if not users:
                print("Nenhum usuário. Crie com: python3 app.py user criar <nome> <senha> --admin")
            for u in users:
                role = "admin" if u["admin"] else "user"
                print(f"  {'●' if u['admin'] else '○'} {u['username']} ({role})")

        elif cmd == "senha":
            if len(sys.argv) < 5:
                print("Uso: python3 app.py user senha <nome> <nova_senha>")
                sys.exit(1)
            r = user_trocar_senha(sys.argv[3], sys.argv[4])
            print(f"{'✅ Senha alterada' if r.get('ok') else '❌ ' + r.get('erro', '')}")

        elif cmd == "deletar":
            if len(sys.argv) < 4:
                print("Uso: python3 app.py user deletar <nome>")
                sys.exit(1)
            r = user_deletar(sys.argv[3])
            print(f"{'✅ Deletado' if r.get('ok') else '❌ ' + r.get('erro', '')}")

        else:
            print(f"Comando desconhecido: {cmd}")

    else:
        import uvicorn
        print()
        print("=" * 55)
        print("  🐟 Tambaqui v2 - IA de Código")
        print("=" * 55)
        print()
        print(f"  Chat:   http://localhost:{PORT}")
        print(f"  Admin:  http://localhost:{PORT}/admin")
        print(f"  API:    http://localhost:{PORT}/v1/chat/completions")
        print(f"  CLI:    python3 app.py chat")
        print()
        print("=" * 55)
        print()
        uvicorn.run(app, host=HOST, port=PORT, log_level="warning", timeout_keep_alive=120)
