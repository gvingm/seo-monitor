# deploy.ps1 — деплой seo-monitor на Beget VPS через Dokploy
# Usage: .\deploy.ps1

$ErrorActionPreference = 'Stop'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'

$API = "https://dokploy.albion1.ru"
$ORG = "FMHJVjL0jRl3sQyWMpQZI"
$TOKEN = "AMAPQXsOWajZIsVBbpkwToZQBCgQPCyXCHdtJXuMWUeUTBolLLKYtSfWPACZzPyG"
$HEADERS = @{
    Authorization  = "Bearer $TOKEN"
    "Content-Type" = "application/json"
    "accept"       = "application/json"
}

$PROJECT_NAME = "SEO Monitor"
$APP_NAME = "seo-monitor"
$REPO_URL = "https://github.com/gvingm/seo-monitor.git"
$BRANCH = "main"
$DOCKERFILE = "./Dockerfile"
$PORT = 8788
$ENVIRONMENT_NAME = "production"

Write-Host "=== SEO Monitor Deploy ===" -ForegroundColor Cyan

# Step 1: Check/create project
Write-Host "Checking project..." -ForegroundColor Yellow

# Step 2: Build Docker image locally and push, OR use GitHub
# For Beget VPS: we'll use GitHub repo approach
# First: create the GitHub repo and push
Write-Host "Step 1: Creating GitHub repository..." -ForegroundColor Yellow

$gh_token = Read-Host "GitHub Personal Access Token (или Enter чтобы пропустить)"
if ($gh_token) {
    $body = @{
        name        = $APP_NAME
        description = "SEO monitor for didalsk.ru — Yandex + Google position tracking"
        private     = $true
    } | ConvertTo-Json

    try {
        $resp = Invoke-RestMethod -Uri "https://api.github.com/user/repos" `
            -Method POST `
            -Headers @{ Authorization = "token $gh_token" } `
            -Body $body `
            -ContentType "application/json"
        Write-Host "✓ GitHub repo created: $($resp.html_url)"
    } catch {
        Write-Host "⚠ GitHub repo: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

# Step 3: Register in Dokploy via API
Write-Host "Step 2: Registering app in Dokploy..." -ForegroundColor Yellow

# Get server list
$servers = Invoke-RestMethod -Uri "$API/api/servers" -Headers $HEADERS
Write-Host "Available servers: $($servers | ConvertTo-Json -Compress)"

# For now: instructions to do manually in Dokploy UI
Write-Host @"

=== MANUAL STEPS IN DOKPLOY UI ===
1. Открой https://dokploy.albion1.ru
2. Projects → Create Project → Name: `$PROJECT_NAME
3. Environments → Add Environment → Name: `$ENVIRONMENT_NAME
4. Applications → Add Application:
   - Name: `$APP_NAME
   - Type: Application (Docker)
   - Repository: $REPO_URL
   - Branch: `$BRANCH
   - Build Path: /
   - Dockerfile: `$DOCKERFILE
   - Port: `$PORT

5. Environment Variables (в Dokploy UI):
   - YANDEX_OAUTH_TOKEN
   - YANDEX_CLOUD_TOKEN
   - YANDEX_FOLDER_ID
   - YANDEX_HOST_ID
   - TELEGRAM_BOT_TOKEN
   - TELEGRAM_CHAT_ID
   - DATABASE_URL: postgresql://seomonitor:ChangeMe123@db:5432/seomonitor

6. Add Database:
   - Type: PostgreSQL
   - Name: seomonitor-db

7. Volumes:
   - /app/creds → для GSC credentials

8. Domain (опционально):
   - seo.didalsk.ru

9. Health check:
   - Path: /health
   - Expected: {"status":"ok"}

=== END MANUAL STEPS ===

"@ -ForegroundColor Magenta

Write-Host "After creating in Dokploy, run Docker directly on Beget VPS:" -ForegroundColor Cyan
Write-Host @"

# SSH to Beget VPS
ssh root@159.194.226.89

# Clone and run
git clone https://github.com/gvingm/seo-monitor.git /opt/seo-monitor
cd /opt/seo-monitor

# Copy .env and fill credentials
cp .env.example .env
nano .env  # заполнить токены

# Start with docker-compose
docker-compose up -d --build

# Check logs
docker-compose logs -f app

# Initialize DB (первый запуск)
docker-compose exec app python init_db.py

"@
