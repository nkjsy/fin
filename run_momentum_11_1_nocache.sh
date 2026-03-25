#!/usr/bin/env bash
set -e
cd /home/nkjsy/fin
. .venv312/bin/activate
python main_momentum_11_1_nocache.py "$@"
