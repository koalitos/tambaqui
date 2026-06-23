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
from typing import List, Optional, Generator

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

import secrets

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
                total = props.total_mem
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
TOKENS: dict = {}  # token -> username (sessões ativas)


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
    TOKENS[token] = username
    return token


def user_validar(token: str) -> Optional[dict]:
    # Sessão de login
    username = TOKENS.get(token)
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
    # Revogar tokens
    for tok, uname in list(TOKENS.items()):
        if uname == username:
            del TOKENS[tok]
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
        self._lock = threading.Lock()

        # Carregar config salva
        cfg = _carregar_config()
        self.device_atual = cfg.get("device", "auto")

    def detectar_devices(self) -> list:
        """Lista devices disponíveis na máquina."""
        devices = [{"id": "cpu", "nome": "CPU", "disponivel": True, "info": f"{os.cpu_count()} cores"}]

        try:
            import torch
            if torch.cuda.is_available():
                for i in range(torch.cuda.device_count()):
                    props = torch.cuda.get_device_properties(i)
                    mem_gb = round(props.total_mem / (1024**3), 1)
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

    # --- Descoberta ---

    def listar_locais(self) -> list:
        """Lista modelos já baixados."""
        modelos = []
        for d in sorted(MODELOS_DIR.iterdir()):
            if not d.is_dir():
                continue
            # Detectar formato
            has_safetensors = any(d.glob("*.safetensors"))
            has_config = (d / "config.json").exists()
            if not (has_safetensors or has_config):
                continue
            tamanho = sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / (1024**3)
            modelos.append({
                "nome": d.name,
                "tamanho_gb": round(tamanho, 1),
                "ativo": d.name == self.modelo_ativo,
                "path": str(d),
            })
        return modelos

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
                total_files = len(files)
                destino.mkdir(parents=True, exist_ok=True)

                # Calcular tamanho total (se possível)
                info = api.model_info(repo)
                total_bytes = 0
                file_sizes = {}
                for s in info.siblings:
                    if s.size:
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
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            # Carregar tokenizer
            self.tokenizer = AutoTokenizer.from_pretrained(
                str(pasta), trust_remote_code=True,
            )
            if not self.tokenizer.pad_token:
                self.tokenizer.pad_token = self.tokenizer.eos_token

            # Device e dtype
            modelo_gb = sum(f.stat().st_size for f in pasta.glob("*.safetensors")) / (1024**3)

            # RAM disponível
            ram_gb = 0
            if HAS_PSUTIL:
                ram_gb = psutil.virtual_memory().available / (1024**3)

            if self.device_atual == "auto":
                device = "cpu"
                dtype = torch.float16  # float16 em CPU funciona e usa metade da RAM
                if torch.cuda.is_available():
                    gpu_mem = torch.cuda.get_device_properties(0).total_mem / (1024**3)
                    if modelo_gb * 1.2 < gpu_mem:
                        device = "cuda"
                elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    # MPS: tentar sempre, deixar o torch lidar com offload
                    device = "mps"
            elif self.device_atual.startswith("cuda"):
                device = self.device_atual
                dtype = torch.float16
            elif self.device_atual == "mps":
                device = "mps"
                dtype = torch.float16
            else:
                device = "cpu"
                dtype = torch.float16

            self.device_nome = device
            ram_modelo = modelo_gb * (2 if dtype == torch.float16 else 4) / modelo_gb if modelo_gb else 0
            logger.info(f"  Device: {device} | Dtype: {dtype} | Modelo: {modelo_gb:.1f}GB | RAM livre: {ram_gb:.1f}GB")

            # Carregar modelo
            load_kwargs = {
                "dtype": dtype,
                "trust_remote_code": True,
                "low_cpu_mem_usage": True,
            }

            # Para modelos grandes: usar device_map auto pra distribuir entre RAM/GPU
            if modelo_gb > 6:
                load_kwargs["device_map"] = "auto"
                logger.info(f"  Modelo grande ({modelo_gb:.1f}GB) - usando device_map=auto")
            elif device != "cpu":
                load_kwargs["device_map"] = device

            try:
                self.model = AutoModelForCausalLM.from_pretrained(str(pasta), **load_kwargs)
            except Exception as e1:
                # Fallback: se falhar (MPS buffer, OOM), tentar CPU float16
                logger.warning(f"  Falha no {device}: {e1}")
                logger.info(f"  Tentando fallback: CPU float16...")
                device = "cpu"
                dtype = torch.float16
                self.device_nome = device
                load_kwargs = {"dtype": dtype, "trust_remote_code": True, "low_cpu_mem_usage": True}
                self.model = AutoModelForCausalLM.from_pretrained(str(pasta), **load_kwargs)

            if not hasattr(self.model, "hf_device_map"):
                self.model = self.model.to(device)
            self.model.eval()

            # --- Otimizações de performance ---

            # 1. BetterTransformer / SDPA (Flash Attention se disponível)
            try:
                self.model = self.model.to_bettertransformer()
                logger.info("  ⚡ BetterTransformer ativado")
            except Exception:
                pass

            # 2. torch.compile (PyTorch 2.0+ - JIT compile, ~2x mais rápido)
            try:
                if hasattr(torch, "compile") and device != "mps":
                    self.model = torch.compile(self.model, mode="reduce-overhead")
                    logger.info("  ⚡ torch.compile ativado")
            except Exception:
                pass

            # 3. KV cache e padding side
            self.tokenizer.padding_side = "left"

            self.modelo_ativo = nome
            # Salvar pra lembrar no modo sob demanda
            cfg = _carregar_config()
            cfg["ultimo_modelo"] = nome
            _salvar_config(cfg)
            n_params = sum(p.numel() for p in self.model.parameters())
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
        if self.model:
            return True
        locais = self.listar_locais()
        if not locais:
            return False
        # Usar último modelo ou config salva ou primeiro disponível
        cfg = _carregar_config()
        nome = self.modelo_ativo or cfg.get("ultimo_modelo") or locais[0]["nome"]
        logger.info(f"🔄 Sob demanda - carregando {nome}...")
        self.carregar(nome)
        return self.model is not None

    def gerar(self, messages: list, max_tokens: int = 2048, temperature: float = 0.7,
              top_p: float = 0.9, stream: bool = False) -> any:
        """Gera resposta. Se stream=True, retorna generator."""
        mode = self.get_load_mode()

        # Sob demanda: carregar antes de gerar
        if mode == "ondemand" and not self.model:
            if not self._auto_carregar():
                return None

        if not self.model or not self.tokenizer:
            return None

        self._resetar_idle_timer()

        if stream:
            return self._gerar_stream_wrapper(messages, max_tokens, temperature, top_p, mode)
        else:
            resultado = self._gerar_completo(messages, max_tokens, temperature, top_p)
            self._limpar_cache()
            # Sob demanda: descarregar depois de responder
            if mode == "ondemand":
                logger.info("📦 Sob demanda - descarregando modelo")
                self.descarregar()
            return resultado

    def _gerar_stream_wrapper(self, messages, max_tokens, temperature, top_p, mode=None):
        """Wrapper do stream que limpa cache e descarrega se ondemand."""
        for chunk in self._gerar_stream(messages, max_tokens, temperature, top_p):
            yield chunk
        self._limpar_cache()
        if mode == "ondemand":
            logger.info("📦 Sob demanda - descarregando modelo")
            self.descarregar()

    def _get_gen_kwargs(self, max_tokens: int, temperature: float, top_p: float) -> dict:
        """Kwargs comuns de geração otimizados."""
        kwargs = {
            "max_new_tokens": max_tokens,
            "pad_token_id": self.tokenizer.eos_token_id,
            "use_cache": True,  # KV cache - acelera geração
            "repetition_penalty": 1.1,
        }
        if temperature > 0:
            kwargs.update({"do_sample": True, "temperature": temperature, "top_p": top_p})
        else:
            kwargs["do_sample"] = False
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

    def _gerar_completo(self, messages: list, max_tokens: int, temperature: float, top_p: float) -> Optional[str]:
        import torch

        try:
            inputs, input_len = self._prepare_inputs(messages)
            gen_kwargs = self._get_gen_kwargs(max_tokens, temperature, top_p)

            with torch.inference_mode():  # mais rápido que no_grad
                outputs = self.model.generate(**inputs, **gen_kwargs)

            resposta = self.tokenizer.decode(outputs[0][input_len:], skip_special_tokens=True)
            return resposta.strip() or None

        except Exception as e:
            logger.error(f"Erro na geração: {e}")
            return None

    def _gerar_stream(self, messages: list, max_tokens: int, temperature: float, top_p: float) -> Generator:
        """Gera tokens um a um (streaming)."""
        import torch
        from transformers import TextIteratorStreamer

        try:
            inputs, _ = self._prepare_inputs(messages)
            streamer = TextIteratorStreamer(self.tokenizer, skip_prompt=True, skip_special_tokens=True)
            gen_kwargs = {**inputs, **self._get_gen_kwargs(max_tokens, temperature, top_p), "streamer": streamer}

            def _gen():
                with torch.inference_mode():
                    self.model.generate(**gen_kwargs)

            thread = threading.Thread(target=_gen)
            thread.start()

            for chunk in streamer:
                if chunk:
                    yield chunk

            thread.join()

        except Exception as e:
            logger.error(f"Erro no streaming: {e}")
            yield f"\n[Erro: {e}]"

    def pronto(self) -> bool:
        return self.model is not None and self.tokenizer is not None

    def status(self) -> dict:
        return {
            "modelo_ativo": self.modelo_ativo,
            "pronto": self.pronto(),
            "carregando": self.carregando,
            "warmup": self.warmup_done,
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

app = FastAPI(title="Tambaqui", version="2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

manager = ModelManager()

# Auto-carregar modelo no startup
@app.on_event("startup")
async def startup():
    locais = manager.listar_locais()
    mode = manager.get_load_mode()
    if locais and mode == "preload":
        logger.info(f"🚀 Pre-load ativado. Carregando {locais[0]['nome']}...")
        threading.Thread(target=lambda: manager.carregar(locais[0]["nome"]), daemon=True).start()
    elif locais and mode == "ondemand":
        logger.info(f"⚡ Modo sob demanda. Modelo carrega quando chegar request.")
    elif locais:
        logger.info(f"📦 {len(locais)} modelo(s). Carregue pelo /admin.")


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
async def api_logout(response: Response):
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

    class Config:
        extra = "allow"  # aceitar campos extras sem erro

class CompletionRequest(BaseModel):
    model: str = ""
    prompt: str = ""
    max_tokens: int = 256
    temperature: float = 0.7
    stream: bool = False
    stop: Optional[List[str]] = None

    class Config:
        extra = "allow"


def _contar_tokens(text: str) -> int:
    """Estimativa simples de tokens (~4 chars por token)."""
    return len(text) // 4


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
    if not manager.pronto():
        # Sob demanda: tentar carregar automaticamente
        if manager.get_load_mode() == "ondemand":
            if not manager._auto_carregar():
                return JSONResponse(status_code=503, content={"error": {"message": "No models available to load", "type": "server_error"}})
        else:
            return JSONResponse(status_code=503, content={"error": {"message": "No model loaded. Open /admin to download and load a model.", "type": "server_error"}})

    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    max_tokens = req.max_tokens or 2048

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

    if req.stream:
        return StreamingResponse(_stream_response(messages, req), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # Resposta completa
    prompt_text = " ".join(m["content"] for m in messages)
    resposta = manager.gerar(messages, max_tokens, req.temperature, req.top_p, stream=False)
    if not resposta:
        resposta = ""

    prompt_tokens = _contar_tokens(prompt_text)
    completion_tokens = _contar_tokens(resposta)

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
    if not manager.pronto():
        if manager.get_load_mode() == "ondemand":
            if not manager._auto_carregar():
                return JSONResponse(status_code=503, content={"error": {"message": "No models available", "type": "server_error"}})
        else:
            return JSONResponse(status_code=503, content={"error": {"message": "No model loaded", "type": "server_error"}})

    messages = [{"role": "user", "content": req.prompt}]
    resposta = manager.gerar(messages, req.max_tokens, req.temperature, stream=False) or ""

    return {
        "id": f"cmpl-{uuid.uuid4().hex[:8]}",
        "object": "text_completion",
        "created": int(time.time()),
        "model": manager.modelo_ativo or "",
        "choices": [{"text": resposta, "index": 0, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": _contar_tokens(req.prompt), "completion_tokens": _contar_tokens(resposta), "total_tokens": _contar_tokens(req.prompt) + _contar_tokens(resposta)},
    }


def _stream_response(messages: list, req: ChatRequest):
    """Gera SSE no formato OpenAI streaming - compatível com todos os CLIs."""
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    model = manager.modelo_ativo or req.model or ""
    created = int(time.time())
    max_tokens = req.max_tokens or 2048

    # Primeiro chunk: role
    yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"

    for chunk in manager.gerar(messages, max_tokens, req.temperature, req.top_p, stream=True):
        data = {
            "id": chat_id, "object": "chat.completion.chunk",
            "created": created, "model": model,
            "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(data)}\n\n"

    # Final
    yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
    yield "data: [DONE]\n\n"


# --- Tambaqui API (chat com busca + sessão) ---

class TambaquiChatRequest(BaseModel):
    mensagem: str
    session_id: Optional[str] = None
    user: str = "default"
    buscar_web: bool = True


@app.post("/api/chat")
async def tambaqui_chat(req: TambaquiChatRequest):
    """Chat Tambaqui com streaming: busca web → stream do modelo token a token."""
    sessao = Sessao(req.session_id, req.user)
    sessao.adicionar("user", req.mensagem)

    def _stream():
        # 1. Buscar contexto web
        contexto = ""
        fontes = []
        if req.buscar_web:
            pesquisa = buscar_web(req.mensagem)
            contexto = pesquisa["contexto"]
            fontes = pesquisa["fontes"]

        # Enviar fontes + session_id primeiro
        yield f"data: {json.dumps({'type': 'meta', 'fontes': fontes, 'session_id': sessao.session_id})}\n\n"

        # 2. Montar messages
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(sessao.get_messages(max_msgs=10))

        if contexto and messages:
            last = messages[-1]
            if last["role"] == "user":
                last["content"] = f"Contexto da pesquisa:\n{contexto[:2500]}\n\n---\n{last['content']}"

        # 3. Stream do modelo
        full_response = ""
        if manager.pronto():
            try:
                for chunk in manager.gerar(messages, max_tokens=2048, stream=True):
                    full_response += chunk
                    yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
            except Exception as e:
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
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
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
