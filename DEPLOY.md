# Deploying GameOS on a VPS

Goal: GameOS runs 24/7 in the cloud — engine + SDK collector + portal, all with HTTPS —
so games can send events from anywhere and you can view the portal from any device.

## 0. What you need
- A small VPS (2 GB RAM is plenty). Hetzner / DigitalOcean / Contabo all fine (~$5/mo).
- A domain (or subdomain) you can point at the VPS, e.g. `gameos.yourdomain.com`.

## 1. Point the domain at the VPS
In your domain's DNS, add an **A record**: `gameos` → your VPS IP address.

## 2. Install Docker on the VPS
SSH in, then:
```bash
curl -fsSL https://get.docker.com | sh
```

## 3. Get the code + secrets
```bash
git clone https://github.com/fojiphoto/StudioPilot.git
cd StudioPilot
cp .env.example .env
```
Now edit `.env` and fill in:
- All the API credentials (copy from your local `.env` — AppLovin, AdMob, Meta, Mintegral, Google Ads, WhatsApp).
- `DOMAIN=gameos.yourdomain.com`
- `POSTGRES_PASSWORD=` any strong password
- `DASHBOARD_USER=` a login name for the portal
(No hash needed in `.env` — the portal password is set in the next step.)

## 4. Render the Caddyfile, then launch
```bash
bash deploy/setup-caddy.sh 'YOUR_PORTAL_PASSWORD'   # reads DOMAIN + DASHBOARD_USER from .env
docker compose up -d --build
```
`setup-caddy.sh` writes a local `Caddyfile` (gitignored) with your domain, portal user
and the hashed password inlined. Re-run it if you change the domain or password.
That starts: Postgres, the engine (pulls + analyzes every 10 min), the collector, the
portal, and Caddy (which gets a free HTTPS certificate for your domain automatically).

## 5. First-run tasks
```bash
# backfill 45 days of history into the cloud DB
docker compose exec engine gameos backfill applovin_max --days 45
docker compose exec engine gameos backfill admob --days 45
docker compose exec engine gameos backfill mintegral --days 45

# mint SDK keys for the games you'll instrument
docker compose exec engine gameos ingest-key --all
docker compose exec engine gameos ingest-key --show   # copy keys into each game's SDK
```

## 6. Use it
- **Portal:** `https://gameos.yourdomain.com/` (log in with DASHBOARD_USER / your password)
- **SDK endpoint** (put in GameOSAnalytics.cs): `https://gameos.yourdomain.com/collect`
- WhatsApp alerts keep working (credentials are in `.env`).

## Updating later
```bash
git pull
docker compose up -d --build
```

## Notes
- The collector (`/collect`) is public by design — games must reach it — but every event
  needs a valid per-game ingest key, so it's safe.
- The portal is behind HTTP basic auth. Only you have the login.
- Data lives in the `pgdata` Docker volume; it survives restarts and redeploys.
- To move hosts, back up the volume (`docker compose exec db pg_dump -U gameos gameos > backup.sql`).
