#!/bin/bash
# Double-click to set up (first time) and start the BuildPact bot.
cd "$(dirname "$0")"
echo "== BuildPact =="
if ! command -v python3 >/dev/null; then
  echo "Python 3 is not installed. Install it from https://www.python.org/downloads/ and try again."
  read -p "Press Enter to close."; exit 1
fi
python3 --version
if [ ! -d .venv ]; then
  echo "First run: creating environment and installing packages (about a minute)..."
  python3 -m venv .venv || { read -p "venv failed. Press Enter to close."; exit 1; }
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -r requirements.txt || { read -p "Install failed. Press Enter to close."; exit 1; }
fi
[ -f .env ] || cp .env.example .env
if ! grep -qE '^DISCORD_TOKEN=.+' .env; then
  echo ""
  echo ">> Paste your bot token after DISCORD_TOKEN= (and Gemini key after GEMINI_API_KEY= if you have one)."
  echo ">> Save the file (Cmd+S), close it, then double-click 'Start BuildPact' again."
  open -e .env
  read -p "Press Enter to close."; exit 0
fi
echo "Running the offline demo check..."
./.venv/bin/python sim.py --quiet | tail -2
echo ""
echo "Starting the bot. Keep this window open during the demo. Press Ctrl+C to stop."
./.venv/bin/python bot.py
read -p "Bot stopped. Press Enter to close."
