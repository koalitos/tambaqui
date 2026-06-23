# 🐟 Tambaqui

IA brasileira de código com API OpenAI-compatível.

Baixa modelos HuggingFace, roda local, chat com busca web, admin web, streaming.

## Instalar

### Linux/macOS (uma linha)

```bash
git clone https://github.com/koalitos/tambaqui.git
cd tambaqui
bash install.sh
```

### Docker

```bash
git clone https://github.com/koalitos/tambaqui.git
cd tambaqui
docker compose up -d
```

Login padrão Docker: `admin` / `tambaqui`

## Usar

```bash
./tambaqui             # Servidor web (API + Admin)
./tambaqui chat        # Chat no terminal
```

- **Chat**: http://localhost:8000
- **Admin**: http://localhost:8000/admin
- **API**: http://localhost:8000/v1/chat/completions

## API OpenAI-Compatível

Funciona com qualquer client que suporte OpenAI API:

```bash
export OPENAI_API_BASE=http://localhost:8000/v1
export OPENAI_API_KEY=seu-token

# Testar
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"crie um hello world em python"}]}'
```

## Gerenciar Users

```bash
./tambaqui user criar nome senha --admin   # Criar admin
./tambaqui user criar nome senha           # Criar user
./tambaqui user listar                     # Listar
./tambaqui user senha nome novaSenha       # Trocar senha
./tambaqui user deletar nome               # Deletar
```

## Modelos

Baixe pelo admin web ou use modelos do catálogo:

| Modelo | RAM | Uso |
|--------|-----|-----|
| qwen2.5-coder-0.5b | 1 GB | Qualquer PC |
| qwen2.5-coder-1.5b | 3 GB | PCs leves |
| qwen2.5-coder-3b | 6 GB | Balanceado |
| qwen2.5-coder-7b | 14 GB | Completo |
| deepseek-coder-1.3b | 3 GB | Código leve |
| deepseek-coder-6.7b | 14 GB | Código completo |

## License

MIT
