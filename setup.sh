#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Post-template setup script for ai-data-registry (macOS / Linux)
# Run this after creating a new repo from the GitHub template.
# It replaces placeholder values and reinitializes the project for your use.
#
# Windows users: run setup.ps1 instead (PowerShell 7+).
# ============================================================================

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BOLD}${CYAN}ai-data-registry template setup${NC}"
echo ""

# --- Check prerequisites ---------------------------------------------------

# 1. pixi (required)
if ! command -v pixi &>/dev/null; then
  echo -e "${RED}pixi is not installed.${NC}"
  echo ""
  echo "Install pixi first:"
  if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "  brew install pixi                                  # Homebrew"
  fi
  echo "  curl -fsSL https://pixi.sh/install.sh | bash       # macOS / Linux"
  echo ""
  echo "Then re-run: ./setup.sh"
  exit 1
fi

echo -e "  ${GREEN}pixi found: $(pixi --version)${NC}"

# 2. Claude Code (recommended)
if command -v claude &>/dev/null; then
  echo -e "  ${GREEN}Claude Code found: $(claude --version 2>/dev/null || echo 'installed')${NC}"
else
  echo -e "  ${YELLOW}Claude Code not found (recommended).${NC}"
  echo ""
  echo "  Install Claude Code for AI-assisted development:"
  echo "    curl -fsSL https://claude.ai/install.sh | bash   # macOS / Linux"
  if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "    brew install --cask claude-code                   # macOS (Homebrew)"
  fi
  echo ""
  echo "  Then start it in your project directory with: claude"
  echo ""
fi

echo ""

# --- Gather info -----------------------------------------------------------

read -rp "$(echo -e "${BOLD}Project name${NC} (e.g. my-geo-project): ")" PROJECT_NAME
if [[ -z "$PROJECT_NAME" ]]; then
  echo "Project name is required." && exit 1
fi

read -rp "$(echo -e "${BOLD}Author name${NC}: ")" AUTHOR_NAME
AUTHOR_NAME="${AUTHOR_NAME:-$(git config user.name 2>/dev/null || echo 'Your Name')}"

read -rp "$(echo -e "${BOLD}Author email${NC}: ")" AUTHOR_EMAIL
AUTHOR_EMAIL="${AUTHOR_EMAIL:-$(git config user.email 2>/dev/null || echo 'you@example.com')}"

read -rp "$(echo -e "${BOLD}Description${NC} (one line): ")" DESCRIPTION
DESCRIPTION="${DESCRIPTION:-Geospatial data processing project}"

read -rp "$(echo -e "${BOLD}Version${NC} [0.1.0]: ")" VERSION
VERSION="${VERSION:-0.1.0}"

echo ""
echo -e "${YELLOW}Applying settings...${NC}"

# --- Replace placeholders in pixi.toml ------------------------------------

sed -i.bak \
  -e "s|name = \"ai-data-registry\"|name = \"${PROJECT_NAME}\"|g" \
  -e "s|authors = \[.*\]|authors = [\"${AUTHOR_NAME} <${AUTHOR_EMAIL}>\"]|g" \
  -e "s|version = \"0.1.0\"|version = \"${VERSION}\"|g" \
  pixi.toml && rm -f pixi.toml.bak

# --- Replace placeholders in CLAUDE.md ------------------------------------

sed -i.bak \
  -e "s|ai-data-registry|${PROJECT_NAME}|g" \
  CLAUDE.md && rm -f CLAUDE.md.bak

# --- Replace in .claude/ agent/skill files that mention the project --------

find .claude -name '*.md' -exec sed -i.bak \
  -e "s|ai-data-registry|${PROJECT_NAME}|g" {} \;
find .claude -name '*.bak' -delete

# --- Replace placeholders in .env.example ----------------------------------

if [[ -f ".env.example" ]]; then
  sed -i.bak \
    -e "s|ai-data-registry|${PROJECT_NAME}|g" \
    .env.example && rm -f .env.example.bak
fi

# --- Clean up template-specific files --------------------------------------

rm -f setup.ps1
rm -f .github/workflows/template-setup.yml

# --- Generate .env from .env.example ----------------------------------------

if [[ -f ".env.example" ]] && [[ ! -f ".env" ]]; then
  echo ""
  read -rp "$(echo -e "${BOLD}Set up local secrets (.env)?${NC} [y/N]: ")" SETUP_ENV
  if [[ "$SETUP_ENV" =~ ^[Yy]$ ]]; then
    cp .env.example .env
    echo -e "  ${GREEN}Created .env from .env.example${NC}"

    read -rp "$(echo -e "  ${BOLD}S3 endpoint URL${NC} (e.g. https://fsn1.your-objectstorage.com): ")" S3_URL
    [[ -n "$S3_URL" ]] && sed -i.bak "s|^S3_ENDPOINT_URL=.*|S3_ENDPOINT_URL=${S3_URL}|" .env && rm -f .env.bak

    read -rp "$(echo -e "  ${BOLD}S3 bucket name${NC}: ")" S3_BKT
    [[ -n "$S3_BKT" ]] && sed -i.bak "s|^S3_BUCKET=.*|S3_BUCKET=${S3_BKT}|" .env && rm -f .env.bak

    read -rp "$(echo -e "  ${BOLD}S3 region${NC}: ")" S3_RGN
    [[ -n "$S3_RGN" ]] && sed -i.bak "s|^S3_REGION=.*|S3_REGION=${S3_RGN}|" .env && rm -f .env.bak

    read -rp "$(echo -e "  ${BOLD}S3 write key ID${NC}: ")" S3_KEY
    [[ -n "$S3_KEY" ]] && sed -i.bak "s|^S3_WRITE_KEY_ID=.*|S3_WRITE_KEY_ID=${S3_KEY}|" .env && rm -f .env.bak

    read -rp "$(echo -e "  ${BOLD}S3 write secret${NC}: ")" S3_SEC
    [[ -n "$S3_SEC" ]] && sed -i.bak "s|^S3_WRITE_SECRET=.*|S3_WRITE_SECRET=${S3_SEC}|" .env && rm -f .env.bak

    echo ""
    echo -e "  ${GREEN}S3 secrets saved to .env${NC}"
    echo -e "  ${YELLOW}For Hetzner/HuggingFace tokens, edit .env manually.${NC}"
    echo -e "  ${YELLOW}For GitHub repo secrets (CI), see docs/secrets-setup.md${NC}"
  else
    echo -e "  ${YELLOW}Skipped. Copy .env.example to .env later when ready.${NC}"
  fi
fi

# --- Push secrets to GitHub (optional) ------------------------------------

if [[ -f ".env" ]]; then
  echo ""
  # Check for gh CLI
  if command -v gh &>/dev/null; then
    echo -e "  ${GREEN}gh CLI found: $(gh --version | head -1)${NC}"

    # Check if authenticated
    if gh auth status &>/dev/null 2>&1; then
      # Auto-detect repo
      REPO=$(gh repo view --json nameWithOwner -q '.nameWithOwner' 2>/dev/null || true)

      if [[ -n "$REPO" ]]; then
        echo ""
        read -rp "$(echo -e "${BOLD}Push secrets from .env to GitHub repo ${CYAN}${REPO}${NC}${BOLD}?${NC} [y/N]: ")" PUSH_SECRETS
        if [[ "$PUSH_SECRETS" =~ ^[Yy]$ ]]; then
          SECRET_COUNT=0
          while IFS='=' read -r key value; do
            # Skip empty lines, comments, and keys without values
            [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
            [[ -z "$value" ]] && continue
            # Strip leading/trailing whitespace from key
            key=$(echo "$key" | xargs)
            if gh secret set "$key" --repo "$REPO" --body "$value" 2>/dev/null; then
              echo -e "  ${GREEN}Set ${key}${NC}"
              SECRET_COUNT=$((SECRET_COUNT + 1))
            else
              echo -e "  ${RED}Failed to set ${key}${NC}"
            fi
          done < .env
          echo ""
          echo -e "  ${GREEN}${SECRET_COUNT} secret(s) pushed to ${REPO}${NC}"
        else
          echo -e "  ${YELLOW}Skipped. Push secrets later with:${NC}"
          echo "    grep -v '^#' .env | grep '.' | while IFS='=' read -r k v; do [ -n \"\$v\" ] && gh secret set \"\$k\" --repo $REPO --body \"\$v\"; done"
        fi
      else
        echo -e "  ${YELLOW}Could not detect GitHub repo. Push secrets manually later.${NC}"
      fi
    else
      echo -e "  ${YELLOW}gh CLI not authenticated. Run 'gh auth login' first to push secrets.${NC}"
    fi
  else
    echo -e "  ${YELLOW}gh CLI not found (optional, for pushing secrets to GitHub).${NC}"
    echo ""
    echo "  Install gh CLI:"
    if [[ "$OSTYPE" == "darwin"* ]]; then
      echo "    brew install gh                                    # macOS (Homebrew)"
    fi
    echo "    curl -fsSL https://cli.github.com/packages/install.sh | bash  # Linux"
    echo ""
    echo "  Then authenticate and push secrets:"
    echo "    gh auth login"
    echo "    grep -v '^#' .env | grep '.' | while IFS='=' read -r k v; do [ -n \"\$v\" ] && gh secret set \"\$k\" --body \"\$v\"; done"
  fi
fi

# --- Install pixi environment ---------------------------------------------

echo ""
echo -e "${YELLOW}Running pixi install...${NC}"
pixi install

# --- Remove this setup script (after everything succeeds) ------------------

rm -f setup.sh

# --- Done ------------------------------------------------------------------

echo ""
echo -e "${GREEN}${BOLD}Done!${NC} Project '${PROJECT_NAME}' is ready."
echo ""
echo "Next steps:"
echo "  1. Review pixi.toml and CLAUDE.md"
echo "  2. Create your first workspace:  /new-workspace <name> <language>"
echo "  3. Commit:  git add -A && git commit -m 'Initialize ${PROJECT_NAME} from template'"
echo ""
