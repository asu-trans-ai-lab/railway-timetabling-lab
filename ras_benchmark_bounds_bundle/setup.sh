#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
mkdir -p solver/cpp/build
g++ -std=c++17 -O2 -Wall -Wextra -pedantic solver/cpp/network_dp.cpp -o solver/cpp/build/network_dp
printf '%s\n' 'PASS: Python environment and native C++ engine installed'
