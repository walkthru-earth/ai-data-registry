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

# --- Check pixi is installed -----------------------------------------------

if ! command -v pixi &>/dev/null; then
  echo -e "${RED}pixi is not installed.${NC}"
  echo ""
  echo "Install pixi first:"
  if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "  brew install pixi          # Homebrew"
  elif [[ "$OSTYPE" == "linux"* ]]; then
    echo "  curl -fsSL https://pixi.sh/install.sh | bash"
  fi
  echo "  curl -fsSL https://pixi.sh/install.sh | bash   # All platforms"
  echo ""
  echo "Then re-run: ./setup.sh"
  exit 1
fi

echo -e "  pixi found: $(pixi --version)"
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

# --- Clean up template-specific files --------------------------------------

rm -f setup.ps1
rm -f .github/workflows/template-setup.yml

# --- Install pixi environment ---------------------------------------------

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
echo "  2. Create your first workspace:  /project:new-workspace <name> <language>"
echo "  3. Commit:  git add -A && git commit -m 'Initialize ${PROJECT_NAME} from template'"
echo ""
