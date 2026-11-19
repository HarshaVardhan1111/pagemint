# Hosting PageMint on Oracle Cloud (Always Free)

This runs PageMint at a public HTTPS address so it works in claude.ai (web, desktop and phone),
with GitHub sign-in, per-user limits and free web search through SearXNG. Everything below can be
done in a browser: Oracle's **Cloud Shell** (the `>_` icon at the top of the Oracle console) gives
you a terminal.

## One-time setup

### 1. Oracle account

Sign up at oracle.com/cloud/free. A card is needed for identity checks; Always Free resources cost
nothing. Pick a home region near you (it cannot be changed later).

### 2. A deploy key (in Cloud Shell)

```bash
ssh-keygen -t ed25519 -f pagemint_deploy -N "" -C pagemint-deploy
```

```bash
cat pagemint_deploy.pub
```

Keep this terminal open: the `.pub` line goes into the setup script (step 3), and the private key
(`cat pagemint_deploy`) goes into GitHub (step 7).

### 3. Create the server

Compute → Instances → **Create instance**:

- Image: **Canonical Ubuntu 24.04**.
- Shape: **Ampere → VM.Standard.A1.Flex**, 2 OCPU and 12 GB memory (more if your free allowance
  allows). If it says "out of capacity", try again later or another availability domain.
  Fallback: the free **VM.Standard.E2.1.Micro** (x86, 1 GB) — then set `COMPOSE_PROFILES=small`
  in step 5 (no JavaScript rendering, no web search).
- SSH keys: let Oracle generate a key pair and **download the private key** (for your own logins).
- Show advanced options → **Initialization script** → paste `deploy/cloud-init.yaml`, after
  replacing `PASTE-YOUR-DEPLOY-PUBLIC-KEY-HERE` with the line from `pagemint_deploy.pub`.

### 4. Fixed address and open ports

- **Reserved public IP** (keeps the same address if the server is ever rebuilt): Networking →
  IP management → Reserved public IPs → Reserve. Then on the instance: Attached VNICs → the VNIC →
  IPv4 addresses → Edit → choose *Reserved public IP*. (Check your account's free limits.)
- **Ports 80 and 443**: Networking → Virtual cloud networks → your VCN → the subnet's security
  list → Add ingress rules: source `0.0.0.0/0`, TCP, destination ports `80` and `443`.

Your PageMint address is the public IP with dashes plus `.sslip.io`, for example
`140-238-1-2.sslip.io` (sslip.io is a free name service that points that name at that IP).

### 5. GitHub sign-in app

On github.com: Settings → Developer settings → **OAuth Apps → New OAuth App**:

- Application name: `PageMint`
- Homepage URL: `https://YOUR-HOST.sslip.io`
- Authorization callback URL: `https://YOUR-HOST.sslip.io/oauth/github/callback`

Create it, then **Generate a new client secret**.

### 6. Fill in the server settings

In Cloud Shell, log in to the server with the key you downloaded in step 3 (upload it with the
Cloud Shell menu first):

```bash
chmod 600 your-key.key && ssh -i your-key.key ubuntu@YOUR-IP
```

```bash
sudo nano /opt/pagemint/.env
```

Set `PAGEMINT_GITHUB_CLIENT_ID` and `PAGEMINT_GITHUB_CLIENT_SECRET`, and check `PAGEMINT_HOST` /
`PAGEMINT_PUBLIC_URL` (the setup script fills them from the server's IP — after attaching a
reserved IP, update both). The secret key and SearXNG secret were generated for you. Leave
`PAGEMINT_ALLOWED_USERS=HarshaVardhan1111` while testing; set it to `*` to open PageMint to everyone.

### 7. GitHub repository secrets

Repository → Settings → Secrets and variables → Actions → **New repository secret**:

- `DEPLOY_HOST`: the server's public IP
- `DEPLOY_SSH_KEY`: the whole private key from `cat pagemint_deploy` (step 2)

### 8. Publish a release

Create a GitHub release (as for PyPI). The `deploy` workflow builds the image, pushes it to GitHub
Container Registry and updates the server. **After the first run**: on GitHub open your profile →
Packages → `pagemint` → Package settings → change visibility to **Public** (so the server can
download it), then re-run the workflow.

### 9. Add the connector in Claude

claude.ai → Settings → **Connectors** → Add custom connector → URL
`https://YOUR-HOST.sslip.io/mcp` → Connect → sign in with GitHub. On the phone it appears once added.

## Backups

Every night at 03:15 UTC `backup.sh` copies the databases to `/opt/pagemint/backups` (14 kept). To
also keep copies off the server: Storage → Buckets → create a bucket → **Pre-authenticated
requests** → create one with *Permit object reads and writes*, and put its URL in
`PAGEMINT_BACKUP_PAR_URL` in `.env`.

## If Oracle deletes the server (idle reclamation) — rebuild in ~30 minutes

1. Repeat step 3 (same cloud-init) and attach the same reserved IP (step 4).
2. Repeat step 6 (copy the GitHub client id/secret into the new `.env`; keep the same
   `PAGEMINT_SECRET_KEY` if you saved it, otherwise users simply sign in again).
3. Restore the newest backup: download `pagemint-*.tar.gz` from your bucket (or old server), then
   on the server:

```bash
cd /opt/pagemint && tar xzf pagemint-YYYYMMDDTHHMMSSZ.tar.gz -C data
```

```bash
cd /opt/pagemint/data && for f in *-*.sqlite3; do sudo mv "$f" "${f%%-*}.sqlite3"; done && sudo chown 10001:10001 *.sqlite3
```

4. Re-run the `deploy` workflow (Actions → deploy → Run workflow).

## Useful commands on the server

```bash
cd /opt/pagemint && docker compose ps
```

```bash
cd /opt/pagemint && docker compose logs --tail 100 pagemint
```

```bash
cd /opt/pagemint && tail -n 50 data/logs/calls.log
```
