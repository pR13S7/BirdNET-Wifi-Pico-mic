#!/bin/bash
sudo systemctl stop birdnet-mic-bridge-2.service

sudo ffmpeg -f s16le -ar 48000 -ac 1 -i tcp://0.0.0.0:5006?listen=1 -t 10 /media/storage/torrents/mic_test_esp32.wav -y

sudo systemctl start birdnet-mic-bridge-2.service
