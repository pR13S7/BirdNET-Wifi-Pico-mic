#!/bin/bash
sudo systemctl stop birdnet-mic-bridge.service

sudo ffmpeg -f s16le -ar 16000 -ac 1 -i tcp://0.0.0.0:5005?listen=1 -t 10 /media/storage/torrents/mic_test_pico.wav -y

sudo systemctl start birdnet-mic-bridge.service
