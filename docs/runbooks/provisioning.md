# Runbook: CT provisioning for MS-0 (operator, one-time)

Live values (CT ID, addresses, CIDR, issuer script paths) live in the private
`homelab` repo. This checklist is the public-safe contract.

1. **CT:** Debian 13 LXC per spec §18.1: 2 vCPU, 4 GiB RAM, 32 GiB rootfs, 512 MiB swap. It appears in `pct list` for auto-monitoring.
2. **Packages:** nginx, certbot + python3-certbot-nginx, PostgreSQL from PGDG, TimescaleDB Community (TSL) from Timescale's packagecloud repo, Tailscale with Tailscale SSH enabled, and uv for deploy/app users.
3. **Users:** `hwradar` app user and `deploy` CI target user. Put `deploy` in group `hwradar`; app dir `/opt/hw-radar/app` owned `deploy:hwradar`. Sudoers: `deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart hw-radar-web.service hw-radar-poller.service`. Also create `/opt/uv` owned `deploy:hwradar` mode `0755` — `deploy-remote.sh` points `UV_PYTHON_INSTALL_DIR`/`UV_CACHE_DIR` there so the uv-managed 3.14 interpreter lives outside `/home` (the app requires Python ≥3.14, Debian 13 ships 3.13; the web/poller units run `ProtectHome=true`, which would hide a `~deploy/.local` interpreter from `hwradar`). Create the static directory that `collectstatic` writes and nginx serves, outside the app root:

   ```bash
   install -d -o root -g root -m 0755 /var/lib/hw-radar
   install -d -o deploy -g deploy -m 0755 /var/lib/hw-radar/staticfiles
   ```

   It must stay outside `/opt/hw-radar` and have no link to group `hwradar` (no setgid, no `hwradar` group, no ACLs). Production settings default `STATIC_ROOT` to this path; `deploy-remote.sh` refuses to deploy if it is missing, not owned and writable by `deploy`, or inside `/opt/hw-radar`, and it enforces modes `0755` (dirs) / `0644` (files) after every `collectstatic`. **Never add `www-data` (nginx) to group `hwradar`** and never grant it ACLs into `/opt/hw-radar`: that group can read the bao-agent secret render (step 5), so either would expose application secrets to the web server (bug 001).
4. **Database bootstrap:** create role/database `hw_radar`, then create extensions `timescaledb` and `pg_trgm` in the DB. App migrations use `IF NOT EXISTS` so these no-op after provisioning.
5. **bao-agent:** onboard as the next `bao-services` consumer per the homelab runbook. The agent renders `DJANGO_SECRET_KEY`, `HW_RADAR_DB_PASSWORD`, DB identifiers if needed, and **`HW_RADAR_ALLOWED_HOSTS`** (the public host, e.g. the deployed FQDN — comma-separated; **required in production**, settings hardcodes no host and fails loud without it; `CSRF_TRUSTED_ORIGINS` is derived from it) to `/run/bao-agent/hw-radar.env` as `root:hwradar 0640`.
6. **systemd:** copy `deploy/systemd/*.service` to `/etc/systemd/system/`, run `systemd-analyze verify /etc/systemd/system/hw-radar-*.service`, then `systemctl daemon-reload && systemctl enable hw-radar-web hw-radar-poller`.
7. **nginx + TLS (Model A — host terminates TLS):** the CT has no public IP, so TLS terminates at the **container host's** front reverse proxy, not on the CT. On the CT: install `deploy/nginx/hw-radar.conf` (plain HTTP `listen 80`, serves `/static/` from `/var/lib/hw-radar/staticfiles/` created in step 3, proxies to gunicorn `127.0.0.1:8000`), `nginx -t`, reload. On the host: add a `hw-radar.l3digital.net` vhost that proxies to the CT's private `IP:80` (host-side config, kept in the private infra repo — never the public repo), create the public DNS A record → host public IP, then issue the cert host-side (HTTP-01 via the host nginx). The host sets `X-Forwarded-Proto=https`; the CT nginx forwards it unchanged so Django's `SECURE_PROXY_SSL_HEADER` sees HTTPS.
8. **GitHub:** create Environment `production` with required reviewer. Environment secrets: `TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET`, `DEPLOY_HOST`, `DEPLOY_USER`.
9. **Tailnet ACL:** ensure `tag:ci` can reach the CT over SSH; add the explicit grant when the scoped-ACL migration lands.
10. **Owner account:** after first deploy, run `uv run python manage.py createsuperuser` on the CT with a password of at least 16 characters.
11. **Backups:** wire the CT subvolume into restic and a TimescaleDB-aware dump block before first real data; keep restore-test discipline.
12. **Consumer AppRole CIDR bind:** include the CT address and verify OpenBao reachability from the CT.

## One-time migration: static files out of `/opt/hw-radar` (bug 001)

Hosts provisioned before this change collected static files into
`/opt/hw-radar/app/staticfiles`, which nginx (`www-data`) cannot read, so
`/static/` returned 403. Before the first deploy that carries the new settings,
copy `deploy/nginx/hw-radar.conf` from the release commit onto the CT (for
example to `/root/hw-radar.conf`; the rsynced copy under `/opt/hw-radar/app`
is still the previous release until the deploy runs), then as root on the CT:

```bash
install -d -o root -g root -m 0755 /var/lib/hw-radar
install -d -o deploy -g deploy -m 0755 /var/lib/hw-radar/staticfiles
# The installed site file is the one still aliasing the old path; expect one match.
site=$(grep -rl 'alias /opt/hw-radar/app/staticfiles/' /etc/nginx/)
echo "$site"
install -o root -g root -m 0644 /root/hw-radar.conf "$site"
nginx -t && systemctl reload nginx
```

Then deploy. The deploy collects into the new directory, and the workflow's
static smoke test must fetch `/static/admin/css/base.css` through the CT nginx
with HTTP 200. Until that deploy finishes, `/static/` returns 404 instead of
403, so admin styling stays broken for that window, as it already was. After
the smoke test passes and the admin renders styled, the legacy
`/opt/hw-radar/app/staticfiles` may be removed (`rm -rf` as root).
