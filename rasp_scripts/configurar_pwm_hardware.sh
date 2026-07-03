#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="${1:-}"

if [[ -z "$CONFIG_PATH" ]]; then
  if [[ -f /boot/firmware/config.txt ]]; then
    CONFIG_PATH="/boot/firmware/config.txt"
  elif [[ -f /boot/config.txt ]]; then
    CONFIG_PATH="/boot/config.txt"
  else
    echo "Nao encontrei /boot/firmware/config.txt nem /boot/config.txt." >&2
    exit 1
  fi
fi

echo "Arquivo de boot: $CONFIG_PATH"

if grep -Eq '^[[:space:]]*dtoverlay=pwm-2chan([[:space:],#]|$)' "$CONFIG_PATH"; then
  echo "Overlay pwm-2chan ja esta configurado."
else
  BACKUP_PATH="${CONFIG_PATH}.agility-backup-$(date +%Y%m%d-%H%M%S)"
  echo "Criando backup em $BACKUP_PATH"
  sudo cp "$CONFIG_PATH" "$BACKUP_PATH"
  echo "Adicionando dtoverlay=pwm-2chan"
  printf '\n# Agility IR hardware PWM on GPIO18/GPIO19\ndtoverlay=pwm-2chan\n' | sudo tee -a "$CONFIG_PATH" >/dev/null
fi

echo
echo "Reinicie a Raspberry para o overlay carregar:"
echo "  sudo reboot"
echo
echo "Depois do reboot, valide com:"
echo "  lsmod | grep pwm"
echo "  ls -la /sys/class/pwm"
echo "  python rasp_scripts/testar_sensor_ir.py --pwm-backend kernel_pwm --freqs 56000 --duration 0.2 --skip-hold"
