# Running the bot 24/7 on a server

The bot runs on an Oracle Cloud "Always Free" server (Ubuntu 24.04, Hyderabad, India),
in **paper mode**: real Binance prices, simulated orders with realistic fees and slippage.
Your laptop can be off. You follow it on **Telegram** and, when you want, on the dashboard.

> Why paper mode and not the testnet? The testnet has so few traders that its prices
> sometimes spike wildly. On 2026-10-02 a fake 12% "crash" for a few seconds hit our
> stop-loss, while the real BTC price never came close. Paper mode checks every
> stop-loss and target against REAL 1-minute prices.

## Everyday commands (from your laptop)

| What | Command |
|---|---|
| Log in to the server | `ssh tradebot` |
| Is the bot running? | `ssh tradebot systemctl status tradebot` |
| Live log (Ctrl+C to leave) | `ssh tradebot journalctl -u tradebot -f -o cat` |
| Accuracy of finished trades | `ssh tradebot "cd tradebot && ~/.local/bin/uv run --no-dev tradebot trades --mode paper"` |
| Cash, positions, live P/L | `ssh tradebot "cd tradebot && ~/.local/bin/uv run --no-dev tradebot status --mode paper"` |
| Restart | `ssh tradebot sudo systemctl restart tradebot` |
| Stop (positions keep their exits) | `ssh tradebot sudo systemctl stop tradebot` |
| Install new code from the laptop | `deploy/deploy.sh` then the restart command |
| Back up the trade database | `scp tradebot:tradebot/data/tradebot_paper.sqlite ./backup-$(date +%F).sqlite` |

## Dashboard from your laptop

The dashboard only listens on the server itself (never exposed to the internet).
Open a secure tunnel, then use your browser:

```bash
ssh -N -L 8001:127.0.0.1:8000 tradebot
```

Leave that running and open **http://127.0.0.1:8001**. Ctrl+C closes the tunnel.

## Telegram

Your phone is the main control: `/status`, `/analyze`, `/trades`, `/pause`, `/trade`,
`/loop on|off`, `/explain on|off`, `/stop confirm`.
Only ONE program can use the Telegram bot at a time. If the bot also runs on your
laptop with Telegram on, you'll see "Conflict" warnings. Stop one of them.

## How it keeps running

- `deploy/tradebot.service` is a systemd service: it starts at boot and restarts
  automatically 30 seconds after any crash.
- Network problems are retried; open positions are saved in SQLite and picked up
  again after a restart.
- The server has 1 GB RAM + 2 GB swap; the bot uses about 350 MB.

## Setting up a server from scratch

1. Create an Ubuntu 24.04 VM (Oracle Always Free E2.1.Micro or A1.Flex) in a region where
   Binance works (not the USA). Download the SSH private key.
2. `cp ~/Downloads/ssh-key-*.key ~/.ssh/oracle_tradebot.key && chmod 600 ~/.ssh/oracle_tradebot.key`
3. Add to `~/.ssh/config`:
   ```
   Host tradebot
       HostName <PUBLIC_IP>
       User ubuntu
       IdentityFile ~/.ssh/oracle_tradebot.key
   ```
4. On the server: add 2 GB swap, install uv
   (`curl -LsSf https://astral.sh/uv/install.sh | sh`), `mkdir ~/tradebot`.
5. From the laptop: `deploy/deploy.sh`, then `ssh tradebot sudo systemctl restart tradebot`.
