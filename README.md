# Website Cloner

An AI-powered website cloning tool. Enter a URL, and it scrapes, screenshots, and uses AI to generate a standalone HTML/CSS clone displayed in a live preview.

**Deployed project:** https://clonr-two.vercel.app/

**Demo video:** `<ADD_VIDEO_LINK>`

## Architecture

```
User → [Next.js Frontend] → [FastAPI Backend] → [OpenRouter LLM]
                                    ↓
                              [Playwright: scrape + screenshot]
                                    ↓
                              [Daytona SDK: sandbox preview]
                                    ↓
                              [Supabase: clone history]
```

## Quick Start (Docker)

```bash
# 1. Copy env files and add your API keys
cp backend/.env.example backend/.env

# 2. Run everything
docker compose up --build
```

- Frontend: http://localhost:3000
- Backend: http://localhost:8000

## Environment Variables

### Backend (`backend/.env`)

| Variable | Description |
|----------|-------------|
| `OPENROUTER_API_KEY` | API key from [OpenRouter](https://openrouter.ai) (required) |
| `SUPABASE_URL` | Supabase project URL (optional — history disabled without it) |
| `SUPABASE_KEY` | Supabase anon key (optional) |
| `DAYTONA_API_KEY` | Daytona API key (optional — falls back to inline preview) |

### Frontend (`frontend/.env.local`)

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_API_URL` | Backend URL (defaults to `http://localhost:8000`) |

## Running Without Docker

### Frontend
```bash
cd frontend
npm install
npm run dev
```

### Backend
```bash
cd backend
pip install -r requirements.txt
playwright install chromium
uvicorn app.main:app --reload
```

## Tech Stack

- **Frontend:** Next.js 14, Tailwind CSS, shadcn/ui components
- **Backend:** FastAPI, Playwright, httpx, BeautifulSoup
- **AI:** OpenRouter API (Claude Sonnet)
- **Sandboxes:** Daytona SDK
- **Database:** Supabase (PostgreSQL)

## Deployment

- **Frontend:** Deploy `frontend/` to Vercel
- **Backend:** Deploy `backend/` to Railway (Dockerfile included)
