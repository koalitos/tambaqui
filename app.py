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
    "Você é Tambaqui, uma IA brasileira especialista em programação e desenvolvimento de software. "
    "Responda sempre em português brasileiro, de forma clara e com exemplos de código quando relevante. "
    "Formate código com blocos markdown (```linguagem). Gere código COMPLETO e funcional."
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


class ModelManager:
    """Gerencia download, carregamento e inferência de modelos HuggingFace."""

    def __init__(self):
        self.model = None
        self.tokenizer = None
        self.modelo_ativo: Optional[str] = None
        self.carregando = False
        self.progresso_download = {}  # {modelo: progresso}
        self.warmup_done = False
        self._lock = threading.Lock()

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

            # Detectar melhor device/dtype
            # Tamanho do modelo em GB
            modelo_gb = sum(f.stat().st_size for f in pasta.glob("*.safetensors")) / (1024**3)

            device = "cpu"
            dtype = torch.float32

            if torch.cuda.is_available():
                gpu_mem = torch.cuda.get_device_properties(0).total_mem / (1024**3)
                if modelo_gb * 1.2 < gpu_mem:
                    device = "cuda"
                    dtype = torch.float16
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                # MPS tem limite de buffer - só usar pra modelos pequenos (<4GB)
                if modelo_gb < 4:
                    device = "mps"
                    dtype = torch.float16

            logger.info(f"  Device: {device} | Dtype: {dtype} | Modelo: {modelo_gb:.1f}GB")

            # Carregar modelo
            load_kwargs = {
                "torch_dtype": dtype,
                "trust_remote_code": True,
                "low_cpu_mem_usage": True,
            }
            if device != "cpu":
                load_kwargs["device_map"] = device

            self.model = AutoModelForCausalLM.from_pretrained(str(pasta), **load_kwargs)
            if device == "cpu":
                self.model = self.model.to(device)
            self.model.eval()

            self.modelo_ativo = nome
            n_params = sum(p.numel() for p in self.model.parameters())
            logger.info(f"✅ Modelo pronto: {nome} ({n_params/1e9:.1f}B params, {device})")

            # Warmup - gerar tokens dummy pra aquecer caches
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
        """Aquece o modelo com uma inferência dummy (como shaders de jogo)."""
        if not self.model or not self.tokenizer:
            return
        logger.info("🔥 Warmup...")
        try:
            import torch
            inputs = self.tokenizer("def hello():", return_tensors="pt")
            if hasattr(self.model, "device"):
                inputs = {k: v.to(self.model.device) for k, v in inputs.items()}
            with torch.no_grad():
                self.model.generate(**inputs, max_new_tokens=10, do_sample=False)
            self.warmup_done = True
            logger.info("🔥 Warmup concluído - modelo quente!")
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

    def gerar(self, messages: list, max_tokens: int = 2048, temperature: float = 0.7,
              top_p: float = 0.9, stream: bool = False) -> any:
        """Gera resposta. Se stream=True, retorna generator."""
        if not self.model or not self.tokenizer:
            return None

        if stream:
            return self._gerar_stream(messages, max_tokens, temperature, top_p)
        else:
            return self._gerar_completo(messages, max_tokens, temperature, top_p)

    def _gerar_completo(self, messages: list, max_tokens: int, temperature: float, top_p: float) -> Optional[str]:
        import torch

        try:
            text = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            inputs = self.tokenizer(text, return_tensors="pt", truncation=True, max_length=4096)
            if hasattr(self.model, "device"):
                inputs = {k: v.to(self.model.device) for k, v in inputs.items()}
            input_len = inputs["input_ids"].shape[1]

            gen_kwargs = {
                "max_new_tokens": max_tokens,
                "pad_token_id": self.tokenizer.eos_token_id,
                "repetition_penalty": 1.1,
            }
            if temperature > 0:
                gen_kwargs.update({"do_sample": True, "temperature": temperature, "top_p": top_p})
            else:
                gen_kwargs["do_sample"] = False

            with torch.no_grad():
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
            text = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            inputs = self.tokenizer(text, return_tensors="pt", truncation=True, max_length=4096)
            if hasattr(self.model, "device"):
                inputs = {k: v.to(self.model.device) for k, v in inputs.items()}

            streamer = TextIteratorStreamer(self.tokenizer, skip_prompt=True, skip_special_tokens=True)

            gen_kwargs = {
                **inputs,
                "max_new_tokens": max_tokens,
                "pad_token_id": self.tokenizer.eos_token_id,
                "streamer": streamer,
                "repetition_penalty": 1.1,
            }
            if temperature > 0:
                gen_kwargs.update({"do_sample": True, "temperature": temperature, "top_p": top_p})
            else:
                gen_kwargs["do_sample"] = False

            thread = threading.Thread(target=lambda: self.model.generate(**gen_kwargs))
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


def buscar_web(query: str) -> dict:
    """Busca em Wikipedia + StackOverflow + DuckDuckGo."""
    topico = _extrair_topico(query)
    lang = _detectar_lang(query)
    contexto = ""
    fontes = []

    # Wikipedia
    try:
        busca = f"{topico} {'programação' if not lang else lang + ' software'}"
        resp = requests.get("https://pt.wikipedia.org/w/api.php", params={
            "action": "query", "list": "search", "srsearch": busca,
            "srlimit": 2, "format": "json", "utf8": 1,
        }, headers=HTTP_HEADERS, timeout=5)
        resultados = resp.json().get("query", {}).get("search", [])
        if resultados:
            titulo = resultados[0]["title"]
            resp2 = requests.get("https://pt.wikipedia.org/w/api.php", params={
                "action": "query", "titles": titulo, "prop": "extracts",
                "explaintext": True, "exintro": True, "format": "json", "utf8": 1,
            }, headers=HTTP_HEADERS, timeout=5)
            for page in resp2.json().get("query", {}).get("pages", {}).values():
                if page.get("extract"):
                    contexto += f"Wikipedia ({titulo}):\n{page['extract'][:2000]}\n\n"
                    fontes.append("Wikipedia")
    except Exception:
        pass

    # StackOverflow
    try:
        tag = f"[{lang}]" if lang else ""
        resp = requests.get("https://api.stackexchange.com/2.3/search/excerpts", params={
            "order": "desc", "sort": "relevance", "q": f"{topico} {tag}",
            "site": "stackoverflow", "pagesize": 3,
        }, headers=HTTP_HEADERS, timeout=5)
        for item in resp.json().get("items", [])[:3]:
            titulo = BeautifulSoup(item.get("title", ""), "html.parser").get_text()
            corpo = BeautifulSoup(item.get("excerpt", ""), "html.parser").get_text()
            contexto += f"StackOverflow: {titulo}\n{corpo[:300]}\n\n"
        if resp.json().get("items"):
            fontes.append("StackOverflow")
    except Exception:
        pass

    # DuckDuckGo
    try:
        q = f"{topico} {lang} code" if lang else f"{topico} programming"
        resp = requests.get("https://lite.duckduckgo.com/lite/", params={"q": q},
                          headers=HTTP_HEADERS, timeout=8)
        soup = BeautifulSoup(resp.text, "html.parser")
        for i, link in enumerate(soup.select("a.result-link")[:5]):
            contexto += f"Web: {link.get_text(strip=True)}\n"
        if soup.select("a.result-link"):
            fontes.append("DuckDuckGo")
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
    if locais:
        threading.Thread(target=lambda: manager.carregar(locais[0]["nome"]), daemon=True).start()


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
    """Retorna a API key do user logado."""
    user = _get_user_from_request(request)
    if not user:
        return JSONResponse(status_code=401, content={"erro": "Não autenticado"})
    key = user_get_api_key(user["username"])
    modelo = manager.modelo_ativo or "nenhum"
    return {
        "api_key": key,
        "username": user["username"],
        "config": {
            "base_url": f"http://localhost:{PORT}/v1",
            "api_key": key,
            "model": modelo,
        },
        "env": f"OPENAI_API_BASE=http://localhost:{PORT}/v1\nOPENAI_API_KEY={key}",
        "curl": f'curl http://localhost:{PORT}/v1/chat/completions \\\n  -H "Content-Type: application/json" \\\n  -H "Authorization: Bearer {key}" \\\n  -d \'{{"model":"{modelo}","messages":[{{"role":"user","content":"oi"}}]}}\'',
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
    content: str

class ChatRequest(BaseModel):
    model: str = ""
    messages: List[ChatMessage]
    temperature: float = 0.7
    max_tokens: int = 2048
    top_p: float = 0.9
    stream: bool = False

class CompletionRequest(BaseModel):
    model: str = ""
    prompt: str
    max_tokens: int = 256
    temperature: float = 0.7
    stream: bool = False


@app.get("/v1/models")
async def list_models():
    """Lista modelos no formato OpenAI."""
    modelos = manager.listar_locais()
    return {
        "object": "list",
        "data": [{"id": m["nome"], "object": "model", "owned_by": "tambaqui"} for m in modelos],
    }


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest, request: Request):
    """Endpoint principal - compatível com OpenAI API."""
    if _auth_ativo() and not _get_user_from_request(request):
        return JSONResponse(status_code=401, content={"error": {"message": "API key inválida. Use Authorization: Bearer tb-...", "type": "auth_error"}})
    if not manager.pronto():
        return JSONResponse(status_code=503, content={"error": {"message": "Modelo não carregado", "type": "server_error"}})

    messages = [m.dict() for m in req.messages]

    if req.stream:
        return StreamingResponse(_stream_response(messages, req), media_type="text/event-stream")

    # Resposta completa
    resposta = manager.gerar(messages, req.max_tokens, req.temperature, req.top_p, stream=False)
    if not resposta:
        resposta = "Erro ao gerar resposta."

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": manager.modelo_ativo or "",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": resposta},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _stream_response(messages: list, req: ChatRequest):
    """Gera SSE no formato OpenAI streaming."""
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"

    for chunk in manager.gerar(messages, req.max_tokens, req.temperature, req.top_p, stream=True):
        data = {
            "id": chat_id, "object": "chat.completion.chunk",
            "created": int(time.time()), "model": manager.modelo_ativo or "",
            "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(data)}\n\n"

    # Final
    data = {
        "id": chat_id, "object": "chat.completion.chunk",
        "created": int(time.time()), "model": manager.modelo_ativo or "",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(data)}\n\n"
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


# --- Pages ---

@app.get("/login")
async def login_page():
    if not _auth_ativo():
        return RedirectResponse("/")
    return HTMLResponse(HTML_LOGIN)

@app.get("/")
async def root(request: Request):
    if _auth_ativo() and not _get_user_from_request(request):
        return RedirectResponse("/login")
    return HTMLResponse(HTML_CHAT)

@app.get("/admin")
async def admin(request: Request):
    if _auth_ativo():
        user = _get_user_from_request(request)
        if not user:
            return RedirectResponse("/login")
        if not user.get("admin"):
            return HTMLResponse("<h1>Acesso negado</h1><p>Apenas admins.</p><a href='/'>Voltar</a>", status_code=403)
    return HTMLResponse(HTML_ADMIN)


# ============================================================
# HTML - LOGIN
# ============================================================

HTML_LOGIN = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Tambaqui - Login</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0d1117;color:#e6edf3;min-height:100vh;display:flex;align-items:center;justify-content:center}
.box{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:40px;width:100%;max-width:380px}
.box h1{font-size:24px;text-align:center;margin-bottom:8px}
.box h1 span{color:#58a6ff}
.box p{text-align:center;color:#8b949e;font-size:13px;margin-bottom:24px}
.field{margin-bottom:16px}
.field label{display:block;font-size:13px;color:#8b949e;margin-bottom:4px}
.field input{width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:10px 12px;color:#e6edf3;font-size:14px;outline:none}
.field input:focus{border-color:#58a6ff}
.btn{width:100%;padding:12px;background:#58a6ff;color:#000;border:none;border-radius:6px;font-size:14px;font-weight:600;cursor:pointer;margin-top:8px}
.btn:hover{background:#79b8ff}
.err{color:#f85149;font-size:13px;text-align:center;margin-top:12px;display:none}
</style>
</head>
<body>
<div class="box">
    <h1>🐟 <span>Tambaqui</span></h1>
    <p>Entre com seu usuário</p>
    <div class="field"><label>Usuário</label><input id="user" autofocus onkeydown="if(event.key==='Enter')document.getElementById('pass').focus()"></div>
    <div class="field"><label>Senha</label><input id="pass" type="password" onkeydown="if(event.key==='Enter')login()"></div>
    <button class="btn" onclick="login()">Entrar</button>
    <div class="err" id="err"></div>
</div>
<script>
async function login(){
    const u=document.getElementById('user').value.trim(),p=document.getElementById('pass').value;
    if(!u||!p)return;
    const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,senha:p})});
    const d=await r.json();
    if(d.ok){window.location.href='/'}
    else{const e=document.getElementById('err');e.textContent=d.erro||'Erro';e.style.display='block'}
}
</script>
</body></html>"""

# ============================================================
# HTML - CHAT
# ============================================================

HTML_CHAT = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Tambaqui</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.0/marked.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0d1117;--sf:#161b22;--bd:#30363d;--tx:#e6edf3;--t2:#8b949e;--ac:#58a6ff;--gn:#3fb950;--or:#d29922;--rd:#f85149}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--tx);min-height:100vh;display:flex}
.side{width:260px;background:var(--sf);border-right:1px solid var(--bd);display:flex;flex-direction:column;flex-shrink:0;overflow:hidden}
.side-hdr{padding:14px 16px;border-bottom:1px solid var(--bd);display:flex;align-items:center;gap:8px}
.side-hdr h2{font-size:14px;flex:1}.side-hdr h2 span{color:var(--ac)}
.side-btn{background:var(--ac);color:#000;border:none;border-radius:6px;padding:5px 10px;font-size:12px;font-weight:600;cursor:pointer}
.side-list{flex:1;overflow-y:auto;padding:8px}
.si{padding:8px 10px;border-radius:6px;cursor:pointer;margin-bottom:2px;font-size:13px;color:var(--t2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.si:hover{background:var(--bg)}.si.act{background:var(--bg);color:var(--tx);border:1px solid var(--bd)}
.si .st-date{font-size:10px;color:var(--t2)}
.side-foot{padding:10px 16px;border-top:1px solid var(--bd);font-size:11px;color:var(--t2)}
.side-foot a{color:var(--ac);text-decoration:none}
.main{flex:1;display:flex;flex-direction:column;min-width:0}
.hdr{background:var(--sf);border-bottom:1px solid var(--bd);padding:10px 20px;display:flex;align-items:center;gap:12px}
.hdr h1{font-size:16px} .hdr h1 span{color:var(--ac)}
.hdr .tag{background:var(--ac);color:#000;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600}
.hdr a{margin-left:auto;color:var(--t2);text-decoration:none;font-size:12px;padding:4px 10px;border:1px solid var(--bd);border-radius:6px}
.hdr a:hover{color:var(--tx)}
.chat{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:16px;max-width:960px;width:100%;margin:0 auto}
.msg{display:flex;gap:10px;animation:fi .3s ease}.msg.u{flex-direction:row-reverse}
.av{width:32px;height:32px;border-radius:50%;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:14px}
.msg.u .av{background:var(--ac);color:#000}.msg.a .av{background:var(--gn);color:#000}
.bb{background:var(--sf);border:1px solid var(--bd);border-radius:12px;padding:16px 20px;max-width:85%;font-size:14px;line-height:1.7;overflow-wrap:break-word}
.msg.u .bb{background:#1a3a5c;border-color:#264a6e;max-width:70%}
.bb h1,.bb h2,.bb h3{color:var(--ac);margin:12px 0 6px;font-size:15px}
.bb p{margin:4px 0}.bb ul,.bb ol{margin:4px 0 4px 20px}
.bb pre{background:#010409;border:1px solid var(--bd);border-radius:8px;padding:14px;margin:8px 0;overflow-x:auto;position:relative}
.bb pre code{font-family:'SF Mono',Consolas,monospace;font-size:13px;line-height:1.5;background:none;padding:0}
.bb code{font-family:'SF Mono',Consolas,monospace;font-size:13px;background:#1c2333;padding:2px 6px;border-radius:4px}
.bb strong{color:var(--ac)}.bb a{color:var(--ac)}
.bb hr{border:none;border-top:1px solid var(--bd);margin:10px 0}
.ft{font-size:11px;color:var(--t2);margin-top:6px;padding-top:6px;border-top:1px solid var(--bd);display:flex;gap:4px;flex-wrap:wrap}
.ft span{background:var(--bg);padding:1px 6px;border-radius:3px;border:1px solid var(--bd)}
@keyframes fi{from{opacity:0;transform:translateY(8px)}to{opacity:1}}
.inp{background:var(--sf);border-top:1px solid var(--bd);padding:12px 20px}
.ibox{max-width:960px;margin:0 auto;display:flex;gap:10px}
.ibox textarea{flex:1;background:var(--bg);border:1px solid var(--bd);border-radius:8px;padding:12px;color:var(--tx);font-size:14px;outline:none;resize:none;min-height:44px;max-height:200px;font-family:inherit}
.ibox textarea:focus{border-color:var(--ac)}
.ibox button{background:var(--ac);color:#000;border:none;border-radius:8px;padding:12px 20px;font-weight:600;cursor:pointer;font-size:14px;align-self:flex-end}
.ibox button:disabled{opacity:.4}
.sbar{max-width:960px;margin:0 auto;padding:4px 0;font-size:11px;color:var(--t2);text-align:center}
.cp{position:absolute;top:6px;right:6px;background:var(--bd);color:var(--t2);border:none;border-radius:4px;padding:2px 6px;font-size:11px;cursor:pointer;opacity:0;transition:.2s}
pre:hover .cp{opacity:1}.cp:hover{background:var(--ac);color:#000}
.typing span{width:6px;height:6px;background:var(--t2);border-radius:50%;display:inline-block;animation:bn .6s infinite alternate;margin:0 2px}
.typing span:nth-child(2){animation-delay:.2s}.typing span:nth-child(3){animation-delay:.4s}
@keyframes bn{to{transform:translateY(-6px);opacity:.5}}
.warn{background:#1a1500;border:1px solid var(--or);border-radius:8px;padding:10px 16px;margin:8px auto;max-width:960px;font-size:13px;color:var(--or);text-align:center}
.warn a{color:var(--ac)}
@media(max-width:700px){.side{display:none}.main{width:100%}}
</style>
</head>
<body>
<div class="side">
    <div class="side-hdr">
        <h2>🐟 <span>Sessões</span></h2>
        <button class="side-btn" onclick="novaSessao()">+ Nova</button>
    </div>
    <div class="side-list" id="sessList"></div>
    <div class="side-foot"><a href="/admin">Admin</a> | <a href="#" onclick="logout()">Sair</a></div>
</div>
<div class="main">
<div class="hdr">
    <h1>🐟 <span>Tambaqui</span></h1>
    <div class="tag">IA de Código</div>
    <a href="/admin">Admin</a>
</div>
<div id="warn"></div>
<div class="chat" id="chat">
    <div class="msg a"><div class="av">🐟</div><div class="bb">
        <p><strong>Tambaqui 🐟</strong> - IA brasileira de código</p>
        <p>Comece a conversar ou selecione uma sessão anterior.</p>
    </div></div>
</div>
<div class="inp">
    <div class="sbar" id="st">Carregando...</div>
    <div class="ibox">
        <textarea id="in" rows="1" placeholder="Pergunte sobre código..."
            onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();go()}"
            oninput="this.style.height='auto';this.style.height=Math.min(this.scrollHeight,200)+'px'"></textarea>
        <button id="btn" onclick="go()">Enviar</button>
    </div>
</div>
</div>
<script>
const R=new marked.Renderer();
R.code=function(c,l){let t=typeof c==='object'?c.text:c,g=typeof c==='object'?c.lang:l;g=g||'';
let h;try{h=g&&hljs.getLanguage(g)?hljs.highlight(t,{language:g}).value:hljs.highlightAuto(t).value}catch(e){h=t}
return`<pre><code class="hljs">${h}</code><button class="cp" onclick="navigator.clipboard.writeText(this.previousElementSibling.textContent);this.textContent='✓'">Copiar</button></pre>`};
marked.setOptions({renderer:R,breaks:true,gfm:true});
let sid=localStorage.getItem('t_sid')||null;
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML}

// Sessões sidebar
async function loadSess(){
    try{
        const r=await fetch('/api/sessoes');const d=await r.json();
        const el=document.getElementById('sessList');
        if(!d.sessoes||!d.sessoes.length){el.innerHTML='<p style="padding:10px;color:var(--t2);font-size:12px">Nenhuma sessão</p>';return}
        el.innerHTML=d.sessoes.map(s=>{
            const act=s.session_id===sid?'act':'';
            const dt=s.atualizada_em?new Date(s.atualizada_em).toLocaleDateString('pt-BR',''):'';
            return`<div class="si ${act}" onclick="carregarSessao('${s.session_id}')" title="${esc(s.titulo)}">
                ${esc(s.titulo||'Sem título')}<br><span class="st-date">${s.mensagens} msgs - ${dt}</span>
            </div>`}).join('');
    }catch(e){}
}

async function carregarSessao(id){
    sid=id;localStorage.setItem('t_sid',sid);
    // Carregar histórico da sessão
    try{
        const r=await fetch('/api/sessoes/'+id);const d=await r.json();
        const ch=document.getElementById('chat');
        ch.innerHTML='';
        if(d.historico){
            for(const m of d.historico){
                if(m.role==='user'){
                    ch.innerHTML+=`<div class="msg u"><div class="av">EU</div><div class="bb"><p>${esc(m.content)}</p></div></div>`;
                }else{
                    ch.innerHTML+=`<div class="msg a"><div class="av">🐟</div><div class="bb">${marked.parse(m.content)}</div></div>`;
                }
            }
            ch.querySelectorAll('pre code').forEach(b=>hljs.highlightElement(b));
            ch.scrollTop=ch.scrollHeight;
        }
    }catch(e){}
    loadSess();
}

function novaSessao(){
    sid=null;localStorage.removeItem('t_sid');
    document.getElementById('chat').innerHTML=`<div class="msg a"><div class="av">🐟</div><div class="bb">
        <p><strong>Nova conversa</strong></p></div></div>`;
    loadSess();
}

async function logout(){
    await fetch('/api/auth/logout',{method:'POST'});
    window.location.href='/login';
}

async function go(){
    const ta=document.getElementById('in'),m=ta.value.trim();if(!m)return;
    const ch=document.getElementById('chat'),bt=document.getElementById('btn');
    ch.innerHTML+=`<div class="msg u"><div class="av">EU</div><div class="bb"><p>${esc(m)}</p></div></div>`;
    ta.value='';ta.style.height='auto';bt.disabled=true;bt.textContent='...';
    const el=document.createElement('div');el.className='msg a';
    el.innerHTML=`<div class="av">🐟</div><div class="bb"><div class="typing"><span></span><span></span><span></span></div></div>`;
    ch.appendChild(el);ch.scrollTop=ch.scrollHeight;
    try{
        const body={mensagem:m,buscar_web:true};if(sid)body.session_id=sid;
        const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        const bb=el.querySelector('.bb');
        bb.innerHTML='';
        let full='',fontes=[];
        const reader=r.body.getReader(),dec=new TextDecoder();
        let buf='';
        while(true){
            const{done,value}=await reader.read();
            if(done)break;
            buf+=dec.decode(value,{stream:true});
            const lines=buf.split('\\n');
            buf=lines.pop();
            for(const line of lines){
                if(!line.startsWith('data: '))continue;
                try{
                    const d=JSON.parse(line.slice(6));
                    if(d.type==='meta'){
                        if(d.session_id){sid=d.session_id;localStorage.setItem('t_sid',sid)}
                        fontes=d.fontes||[];
                    }else if(d.type==='chunk'){
                        full+=d.content;
                        bb.innerHTML=marked.parse(full);
                        bb.querySelectorAll('pre code').forEach(b=>hljs.highlightElement(b));
                        ch.scrollTop=ch.scrollHeight;
                    }else if(d.type==='done'){
                        let ft='';if(fontes.length)ft=`<div class="ft">${fontes.map(f=>`<span>${f}</span>`).join('')}</div>`;
                        bb.innerHTML=marked.parse(full)+ft;
                        bb.querySelectorAll('pre code').forEach(b=>hljs.highlightElement(b));
                        loadSess();
                    }
                }catch(pe){}
            }
        }
    }catch(e){el.querySelector('.bb').innerHTML=`<p style="color:var(--rd)">Erro: ${e}</p>`}
    bt.disabled=false;bt.textContent='Enviar';ch.scrollTop=ch.scrollHeight;
}

async function sts(){
    try{const r=await fetch('/api/status'),d=await r.json(),e=document.getElementById('st'),w=document.getElementById('warn');
    if(!d.pronto&&!d.carregando){w.innerHTML='<div class="warn">Nenhum modelo carregado. <a href="/admin">Abra o Admin</a> para baixar e ativar.</div>'}
    else{w.innerHTML=''}
    const m=d.pronto?`🐟 ${d.modelo_ativo}`:(d.carregando?'Carregando...':'Sem modelo');
    const cpu=d.cpu_percent!==undefined?` | CPU ${d.cpu_percent}%`:'';
    const ram=d.ram_percent!==undefined?` | RAM ${d.ram_percent}%`:'';
    e.textContent=`${m}${cpu}${ram}`}catch(e){}
}
loadSess();sts();setInterval(sts,5000);
</script>
</body></html>"""

# ============================================================
# HTML - ADMIN
# ============================================================

HTML_ADMIN = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Tambaqui Admin</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0d1117;--sf:#161b22;--bd:#30363d;--tx:#e6edf3;--t2:#8b949e;--ac:#58a6ff;--gn:#3fb950;--or:#d29922;--rd:#f85149}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--tx)}
.hdr{background:var(--sf);border-bottom:1px solid var(--bd);padding:12px 20px;display:flex;align-items:center;gap:12px}
.hdr h1{font-size:18px}.hdr h1 span{color:var(--ac)}
.hdr a{color:var(--t2);text-decoration:none;font-size:13px;padding:4px 10px;border:1px solid var(--bd);border-radius:6px}
.ct{max-width:960px;margin:20px auto;padding:0 20px}
.card{background:var(--sf);border:1px solid var(--bd);border-radius:8px;padding:20px;margin-bottom:16px}
.card h2{font-size:16px;margin-bottom:12px;color:var(--ac)}
.sg{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}
.st{background:var(--bg);border:1px solid var(--bd);border-radius:6px;padding:10px;text-align:center}
.st .n{font-size:22px;font-weight:700;color:var(--ac)}.st .l{font-size:11px;color:var(--t2);margin-top:2px}
.btn{padding:8px 16px;border:none;border-radius:6px;font-weight:600;cursor:pointer;font-size:13px;margin:3px}
.btn-a{background:var(--ac);color:#000}.btn-g{background:var(--gn);color:#000}.btn-r{background:var(--rd);color:#fff}.btn-s{padding:5px 10px;font-size:12px}
.btn:disabled{opacity:.4;cursor:not-allowed}
.ml{display:flex;flex-direction:column;gap:8px;margin-top:10px}
.mi{display:flex;align-items:center;gap:10px;background:var(--bg);border:1px solid var(--bd);border-radius:6px;padding:10px}
.mi.act{border-color:var(--gn)}
.mi .nm{font-weight:600;flex:1}.mi .mt{font-size:12px;color:var(--t2)}
.mi .badge{font-size:11px;padding:2px 8px;border-radius:4px;background:var(--gn);color:#000;font-weight:600}
.prog{height:4px;background:var(--bd);border-radius:2px;margin-top:6px;overflow:hidden}
.prog .bar{height:100%;background:var(--ac);transition:width .3s}
.toast{position:fixed;bottom:20px;right:20px;background:var(--gn);color:#000;padding:12px 20px;border-radius:8px;font-weight:600;display:none;z-index:99}
.api-info{background:var(--bg);border:1px solid var(--bd);border-radius:6px;padding:12px;font-family:monospace;font-size:13px;margin-top:10px;color:var(--t2)}
.api-info code{color:var(--ac)}
</style>
</head>
<body>
<div class="hdr"><h1>🐟 <span>Tambaqui</span> Admin</h1><a href="/">Chat</a></div>
<div class="ct">

<div class="card"><h2>Status</h2><div class="sg" id="sts"></div></div>

<div class="card"><h2>Sistema</h2><div class="sg" id="sys"></div></div>

<div class="card">
<h2>Modelos Baixados</h2>
<div class="ml" id="locais"></div>
</div>

<div class="card">
<h2>Baixar Modelo</h2>
<p style="font-size:13px;color:var(--t2);margin-bottom:10px">Escolha um modelo de código. Menores rodam em PCs fracos.</p>
<div class="ml" id="catalogo"></div>
</div>

<div class="card" id="usersCard" style="display:none">
<h2>Usuários</h2>
<div class="ml" id="userList"></div>
<div style="display:flex;gap:8px;align-items:center;margin-top:10px;flex-wrap:wrap">
    <input type="text" id="newUser" placeholder="Usuário" style="background:var(--bg);border:1px solid var(--bd);border-radius:6px;padding:6px 10px;color:var(--tx);font-size:13px">
    <input type="password" id="newPass" placeholder="Senha" style="background:var(--bg);border:1px solid var(--bd);border-radius:6px;padding:6px 10px;color:var(--tx);font-size:13px">
    <label style="font-size:12px;color:var(--t2)"><input type="checkbox" id="newAdmin"> Admin</label>
    <button class="btn btn-g btn-s" onclick="criarUser()">Criar</button>
</div>
</div>

<div class="card">
<h2>Sua API Key</h2>
<div class="api-info" id="apiKeyInfo">Carregando...</div>
<button class="btn btn-a" onclick="regenKey()" style="margin-top:10px">Regenerar API Key</button>
</div>

<div class="card">
<h2>Como Conectar</h2>
<div class="api-info" id="connectInfo">Carregando...</div>
</div>

</div>
<div class="toast" id="toast"></div>
<script>
function toast(m,c){const t=document.getElementById('toast');t.textContent=m;t.style.background=c||'var(--gn)';t.style.display='block';setTimeout(()=>t.style.display='none',3000)}
function bc(p){return p>80?'var(--rd)':p>60?'var(--or)':'var(--gn)'}

async function load(){
    const r=await fetch('/api/status'),d=await r.json();
    document.getElementById('sts').innerHTML=`
        <div class="st"><div class="n" style="font-size:16px">${d.modelo_ativo||'nenhum'}</div><div class="l">Modelo Ativo</div></div>
        <div class="st"><div class="n" style="color:${d.pronto?'var(--gn)':'var(--rd)'}">${d.pronto?'Pronto':'Off'}</div><div class="l">Status</div></div>
        <div class="st"><div class="n" style="color:${d.warmup?'var(--gn)':'var(--t2)'}">${d.warmup?'Quente':'Frio'}</div><div class="l">Pre-load</div></div>
        <div class="st"><div class="n" style="color:${d.carregando?'var(--or)':'var(--t2)'}">${d.carregando?'Sim':'Não'}</div><div class="l">Carregando</div></div>
    `;
    document.getElementById('sys').innerHTML=`
        <div class="st"><div class="n" style="color:${bc(d.cpu_percent||0)}">${d.cpu_percent||0}%</div><div class="l">CPU (${d.cpu_count} cores)</div></div>
        <div class="st"><div class="n" style="color:${bc(d.ram_percent||0)}">${d.ram_percent||0}%</div><div class="l">RAM (${d.ram_usada_gb||0}/${d.ram_total_gb||0} GB)</div></div>
        <div class="st"><div class="n">${d.proc_ram_mb||0}MB</div><div class="l">Processo</div></div>
        <div class="st"><div class="n" style="font-size:14px">${d.platform||''}</div><div class="l">Python ${d.python||''}</div></div>
    `;

    // Modelos locais
    const r2=await fetch('/api/modelos'),d2=await r2.json();
    const loc=document.getElementById('locais');
    if(!d2.locais.length){loc.innerHTML='<p style="color:var(--t2)">Nenhum modelo baixado</p>'}
    else{loc.innerHTML=d2.locais.map(m=>`
        <div class="mi ${m.ativo?'act':''}">
            <div class="nm">${m.nome} ${m.ativo?'<span class="badge">ativo</span>':''}</div>
            <div class="mt">${m.tamanho_gb} GB</div>
            ${!m.ativo?`<button class="btn btn-a btn-s" onclick="carregar('${m.nome}')">Carregar</button>`:''}
            ${!m.ativo?`<button class="btn btn-r btn-s" onclick="deletar('${m.nome}')">X</button>`:''}
            ${m.ativo?`<button class="btn btn-r btn-s" onclick="descarregar()">Descarregar</button>`:''}
        </div>`).join('')}

    // Catálogo com barra de progresso
    const cat=document.getElementById('catalogo');
    cat.innerHTML=d2.catalogo.map(m=>{
        const dl=d.downloads&&d.downloads[m.nome];
        let btn='',prog='';
        if(m.baixado){
            btn='<span style="color:var(--gn);font-size:12px">✅ Baixado</span>';
        }else if(dl){
            const pct=dl.percent||0;
            const arq=dl.arquivo||'';
            const vel=dl.velocidade||'';
            const st=dl.status||'';
            const cor=pct<0?'var(--rd)':pct>=100?'var(--gn)':'var(--ac)';
            btn=`<span style="color:var(--or);font-size:12px;min-width:50px;text-align:right">${pct>=0?pct+'%':st}</span>`;
            prog=`<div style="margin-top:6px">
                <div class="prog"><div class="bar" style="width:${Math.max(pct,0)}%;background:${cor}"></div></div>
                <div style="font-size:10px;color:var(--t2);margin-top:3px">${arq} ${vel?'| '+vel:''}</div>
            </div>`;
        }else{
            btn=`<button class="btn btn-g btn-s" onclick="baixar('${m.nome}')">Baixar</button>`;
        }
        return`<div class="mi" style="flex-wrap:wrap">
            <div class="nm">${m.nome}<br><span style="font-size:11px;color:var(--t2)">${m.desc} | ${m.params} | ${m.ram} RAM</span></div>
            ${btn}
            ${prog?`<div style="width:100%">${prog}</div>`:''}
        </div>`}).join('');
}

async function baixar(n){const r=await fetch('/api/modelos/baixar?nome='+n,{method:'POST'});const d=await r.json();toast(d.status||d.erro);load()}
async function carregar(n){const r=await fetch('/api/modelos/carregar?nome='+n,{method:'POST'});const d=await r.json();toast(d.status||'Carregando...');load()}
async function descarregar(){await fetch('/api/modelos/descarregar',{method:'POST'});toast('Descarregado');load()}
async function deletar(n){if(!confirm('Deletar '+n+'?'))return;await fetch('/api/modelos/'+n,{method:'DELETE'});toast('Deletado');load()}

// API Key + Config
async function loadApiKey(){
    try{
        const r=await fetch('/api/auth/api-key');
        if(r.status!==200)return;
        const d=await r.json();
        document.getElementById('apiKeyInfo').innerHTML=`
            <p>API Key: <code style="user-select:all">${d.api_key}</code>
            <button class="cp" style="position:static;opacity:1;margin-left:4px" onclick="navigator.clipboard.writeText('${d.api_key}');this.textContent='✓'">Copiar</button></p>
            <p style="margin-top:4px;font-size:12px;color:var(--t2)">User: ${d.username}</p>
        `;
        const c=d.config;
        document.getElementById('connectInfo').innerHTML=`
            <p><strong>Variáveis de ambiente:</strong></p>
            <pre style="background:var(--bg);padding:10px;margin:8px 0;border-radius:4px"><code>OPENAI_API_BASE=${c.base_url}
OPENAI_API_KEY=${c.api_key}</code></pre>
            <p><strong>Modelo ativo:</strong> <code>${c.model||'nenhum'}</code></p>
            <p style="margin-top:10px"><strong>Teste com curl:</strong></p>
            <pre style="background:var(--bg);padding:10px;margin:8px 0;border-radius:4px;white-space:pre-wrap;font-size:12px"><code>${d.curl}</code></pre>
            <p style="margin-top:10px"><strong>Clients compatíveis:</strong></p>
            <ul style="margin:4px 0 0 16px;font-size:13px;color:var(--t2)">
                <li>Open WebUI - coloque a Base URL e API Key</li>
                <li>Continue.dev (VS Code) - configure provider custom</li>
                <li>LibreChat - adicione endpoint OpenAI custom</li>
                <li>Qualquer client com suporte a API OpenAI</li>
            </ul>
        `;
    }catch(e){}
}
async function regenKey(){
    if(!confirm('Regenerar API key? A key anterior vai parar de funcionar.'))return;
    await fetch('/api/auth/api-key/regenerar',{method:'POST'});
    toast('API key regenerada');
    loadApiKey();
}

// Users
async function loadUsers(){
    try{
        const r=await fetch('/api/auth/users');
        if(r.status===403){document.getElementById('usersCard').style.display='none';return}
        const d=await r.json();
        document.getElementById('usersCard').style.display='block';
        const el=document.getElementById('userList');
        if(!d.users||!d.users.length){el.innerHTML='<p style="color:var(--t2)">Nenhum usuário</p>';return}
        el.innerHTML=d.users.map(u=>`
            <div class="mi" style="flex-wrap:wrap">
                <div class="nm">${u.username} ${u.admin?'<span class="badge">admin</span>':''}</div>
                <div class="mt">${u.criado_em?new Date(u.criado_em).toLocaleDateString('pt-BR'):''}</div>
                <button class="btn btn-a btn-s" onclick="trocarSenha('${u.username}')">Senha</button>
                <button class="btn btn-r btn-s" onclick="deletarUser('${u.username}')">X</button>
                <div style="width:100%;margin-top:4px;font-size:11px;color:var(--t2)">Key: <code style="user-select:all">${u.api_key||'sem key'}</code></div>
            </div>`).join('');
    }catch(e){}
}
async function criarUser(){
    const u=document.getElementById('newUser').value.trim(),p=document.getElementById('newPass').value,a=document.getElementById('newAdmin').checked;
    if(!u||!p){toast('Preencha usuário e senha','var(--rd)');return}
    const r=await fetch('/api/auth/users',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,senha:p,admin:a})});
    const d=await r.json();
    if(d.ok){toast('Usuário criado');document.getElementById('newUser').value='';document.getElementById('newPass').value=''}
    else toast(d.erro,'var(--rd)');
    loadUsers();
}
async function trocarSenha(u){
    const s=prompt('Nova senha para '+u+':');
    if(!s)return;
    const r=await fetch('/api/auth/trocar-senha',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,nova_senha:s})});
    const d=await r.json();toast(d.ok?'Senha alterada':d.erro);
}
async function deletarUser(u){
    if(!confirm('Deletar usuário '+u+'?'))return;
    await fetch('/api/auth/users/'+u,{method:'DELETE'});toast('Deletado');loadUsers();
}

load();loadUsers();loadApiKey();
// Refresh mais rápido quando tem download ativo
setInterval(()=>{
    const hasDownload=document.querySelector('.bar');
    load();
    if(!hasDownload)return;
},2000);
</script>
</body></html>"""

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
        uvicorn.run(app, host=HOST, port=PORT)
