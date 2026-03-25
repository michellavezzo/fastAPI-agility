#!/bin/bash
# Instala dependências do projeto Agility

python3 -m pip install --upgrade pip

# Instala pacotes do requirements.txt
pip install -r requirements.txt

# Instala pacote para controle do GPIO na Raspberry Pi
pip install RPi.GPIO

echo "Instalação concluída!"


# Tutorial:

# Salve como install_agility.sh, 

# dê permissão de execução com 
# chmod +x install_agility.sh 
# e execute com ./install_agility.sh na Raspberry Pi.