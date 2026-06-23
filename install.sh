#!/bin/bash
# ============================================
# Tambaqui - Instalador Linux/macOS
# bash install.sh
# ============================================

set -e

echo ""
echo "🐟 Tambaqui - Instalando..."
echo "================================"
echo ""

# Verificar Python
if ! command -v python3 &>/dev/null; then
    echo "Instalando Python..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get update && sudo apt-get install -y python3 python3-pip python3-venv
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y python3 python3-pip
    elif command -v pacman &>/dev/null; then
        sudo pacman -S --noconfirm python python-pip
    elif command -v brew &>/dev/null; then
        brew install python3
    else
        echo "❌ Instale Python 3.9+ manualmente"
        exit 1
    fi
fi

PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "✅ Python $PYVER"

# Diretório de instalação
INSTALL_DIR="${TAMBAQUI_DIR:-$(pwd)}"
cd "$INSTALL_DIR"

# Criar venv
if [ ! -d "venv" ]; then
    echo "Criando ambiente virtual..."
    python3 -m venv venv
fi
source venv/bin/activate

# Instalar dependências
echo "Instalando dependências..."
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet 2>/dev/null || pip install \
    torch transformers safetensors accelerate huggingface_hub \
    fastapi uvicorn requests beautifulsoup4 psutil pydantic --quiet

echo "✅ Dependências instaladas"

# Baixar modelo leve (se nenhum existe)
mkdir -p modelos dados/sessoes
if [ -z "$(ls -A modelos/ 2>/dev/null)" ]; then
    echo ""
    echo "Baixando modelo leve (Qwen2.5-Coder-0.5B - ~1GB)..."
    python3 -c "
from huggingface_hub import snapshot_download
snapshot_download('Qwen/Qwen2.5-Coder-0.5B-Instruct', local_dir='modelos/qwen2.5-coder-0.5b')
print('✅ Modelo baixado')
"
fi

# Criar user admin (se não existe)
if [ ! -f "dados/users.json" ]; then
    echo ""
    echo "Criando usuário admin..."
    read -p "  Usuário admin: " ADMIN_USER
    ADMIN_USER=${ADMIN_USER:-admin}
    read -s -p "  Senha: " ADMIN_PASS
    echo ""
    ADMIN_PASS=${ADMIN_PASS:-tambaqui}
    python3 app.py user criar "$ADMIN_USER" "$ADMIN_PASS" --admin
fi

# Script de atalho
cat > tambaqui <<'SCRIPT'
#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/venv/bin/activate"
cd "$DIR"
python3 app.py "$@"
SCRIPT
chmod +x tambaqui

echo ""
echo "================================"
echo "🐟 Tambaqui instalado!"
echo "================================"
echo ""
echo "  Iniciar:    ./tambaqui"
echo "  Chat CLI:   ./tambaqui chat"
echo "  Admin:      http://localhost:8000/admin"
echo "  API OpenAI: http://localhost:8000/v1/chat/completions"
echo ""
echo "  Gerenciar users:"
echo "    ./tambaqui user criar <nome> <senha> [--admin]"
echo "    ./tambaqui user listar"
echo ""
