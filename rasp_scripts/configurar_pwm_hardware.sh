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
echo "Instalando servico de permissao para /sys/class/pwm"
sudo tee /usr/local/sbin/agility-pwm-permissions.sh >/dev/null <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

PWM_LINK="/sys/class/pwm/pwmchip0"

for _ in $(seq 1 50); do
  [[ -d "$PWM_LINK" ]] && break
  sleep 0.1
done

[[ -d "$PWM_LINK" ]] || exit 0

PWM_REAL="$(realpath "$PWM_LINK")"

chgrp -R gpio "$PWM_REAL"
chmod -R g+rwX "$PWM_REAL"

if [[ ! -d "$PWM_REAL/pwm0" ]]; then
  echo 0 > "$PWM_LINK/export" 2>/dev/null || true
fi

chgrp -R gpio "$PWM_REAL"
chmod -R g+rwX "$PWM_REAL"
EOF

sudo chmod +x /usr/local/sbin/agility-pwm-permissions.sh
sudo tee /etc/systemd/system/agility-pwm-permissions.service >/dev/null <<'EOF'
[Unit]
Description=Agility IR PWM sysfs permissions
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/agility-pwm-permissions.sh

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable agility-pwm-permissions.service >/dev/null
sudo systemctl start agility-pwm-permissions.service || true

echo
echo "Reinicie a Raspberry para o overlay carregar:"
echo "  sudo reboot"
echo
echo "Depois do reboot, valide com:"
echo "  lsmod | grep pwm"
echo "  ls -la /sys/class/pwm"
echo "  python rasp_scripts/testar_sensor_ir.py --pwm-backend kernel_pwm --freqs 56000 --duration 0.2 --skip-hold"
