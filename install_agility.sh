#!/bin/bash
# Instala dependências do projeto Agility

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

resolve_python_bin() {
  local env_dir="$1"

  if [ -x "$SCRIPT_DIR/$env_dir/bin/python" ]; then
    echo "$SCRIPT_DIR/$env_dir/bin/python"
  elif [ -x "$SCRIPT_DIR/$env_dir/bin/python3" ]; then
    echo "$SCRIPT_DIR/$env_dir/bin/python3"
  else
    return 1
  fi
}

VENV_DIR=""
PYTHON_BIN=""

if [ -d "venv" ]; then
  VENV_DIR="venv"
  PYTHON_BIN="$(resolve_python_bin "$VENV_DIR" || true)"
fi

if [ -z "$PYTHON_BIN" ] && [ -d "fastapi-env" ]; then
  VENV_DIR="fastapi-env"
  PYTHON_BIN="$(resolve_python_bin "$VENV_DIR" || true)"
fi

if [ -z "$PYTHON_BIN" ]; then
  VENV_DIR="venv"
  python3 -m venv "$VENV_DIR"
  PYTHON_BIN="$(resolve_python_bin "$VENV_DIR")"
fi

"$PYTHON_BIN" -m pip install --upgrade pip

# Instala pacotes do requirements.txt no mesmo ambiente que executa o backend
"$PYTHON_BIN" -m pip install -r requirements.txt

# Instala pacote para controle do GPIO na Raspberry Pi
if ! "$PYTHON_BIN" -m pip install RPi.GPIO; then
  echo "Aviso: RPi.GPIO não foi instalado. Em ambientes fora da Raspberry Pi, isso é esperado."
fi

echo "Instalação concluída!"
echo "Para PWM hardware via pigpio, o daemon pigpiod precisa existir e estar ativo."
echo "Se a imagem Debian/Raspberry Pi OS nao fornecer pigpiod, use AGILITY_IR_PWM_BACKEND=auto para fallback em RPi.GPIO.PWM."
echo "Use: source $VENV_DIR/bin/activate"


# Tutorial:

# Salve como install_agility.sh, 

# dê permissão de execução com 
# chmod +x install_agility.sh 
# e execute com ./install_agility.sh na Raspberry Pi.
