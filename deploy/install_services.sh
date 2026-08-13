#!/usr/bin/env bash
set -e

echo "Installing Hobby Server Monitor Systemd Services..."
sudo cp deploy/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable lxd-monitor-backend.service lxd-monitor-collector.service lxd-monitor-dashboard.service
sudo systemctl restart lxd-monitor-backend.service lxd-monitor-collector.service lxd-monitor-dashboard.service

echo "All services installed and started successfully!"
sudo systemctl status lxd-monitor-backend.service lxd-monitor-collector.service lxd-monitor-dashboard.service --no-pager
