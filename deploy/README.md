# Deploying

Pick one:

**Docker** - `docker compose up -d` runs the hub and both example agents. Data is in `./data`.

**systemd on a VPS**
```sh
sudo useradd -r -m -d /opt/crew crew
sudo -u crew git clone <your repo> /opt/crew && cd /opt/crew && sudo -u crew ./setup.sh
sudo cp deploy/crew-hub.service deploy/crew-agent@.service /etc/systemd/system/
sudo mkdir -p /etc/crew   # one env file per agent: CREW_URL, AGENT_TOKEN_<KEY>, ANTHROPIC_API_KEY
sudo systemctl daemon-reload && sudo systemctl enable --now crew-hub crew-agent@manager crew-agent@researcher
```

**Outside access** - keep the hub on 127.0.0.1 and put HTTPS in front: Caddy (`Caddyfile.example`), nginx, or a
Cloudflare Tunnel (`cloudflared tunnel --url http://127.0.0.1:8000` for a quick test, a named tunnel for real use).
Then set `PUBLIC_URL`, `ALLOWED_HOSTS` and `TRUST_PROXY_HEADERS=1`.

**Agents on other machines** - an agent needs only `CREW_URL=https://your-hub` and its own `AGENT_TOKEN_<KEY>`.
The token opens that agent's sync endpoint and nothing else, so a compromised agent box cannot read the dashboard.

**Backups** - everything is in `data/crew.db` (SQLite). `sqlite3 data/crew.db ".backup backup.db"` is safe while running.
