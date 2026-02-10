# Website Cloning Tool

## Project Overview

Build a website cloning tool inspired by [orchids.app](https://orchids.app). The tool takes a URL as input, clones the website, and displays the result in a sandbox. Reference video: https://screen.studio/share/OYzOPrrF

Check out Orchids, Same.new, or similar cloning tools to understand the expected UX before building.

## Required Features

- **URL Input**: Text box to input the website URL to clone
- **Sandbox Display**: Sandbox/preview to display the cloned website
- Everything else (speed, accuracy, reliability, polish) are nice-to-haves but improve quality

## Stack

- **Frontend**: Next.js (in `frontend/`)
- **Backend**: FastAPI (in `backend/`)
- **Database**: Supabase (schema in `db/schema.sql`)
- **Component Library**: shadcn/ui (recommended)

## Recommended Tools & Services

- **AI Model Calls**: Use [OpenRouter](https://openrouter.ai) for model API calls
- **Sandboxes**: [Daytona](https://www.daytona.io/) for sandboxed environments
- **Code Application**: [Relace](https://relace.ai/) for applying code quickly
- **Hosting (Backend)**: Railway or AWS/GCP

## Project Structure

```
frontend/     → Next.js app
backend/      → FastAPI server
db/           → Supabase schema
```

## Running Locally

```bash
# Frontend
cd frontend && npm install && npm run dev
# → http://localhost:3000

# Backend
cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload
# → http://localhost:8000
```

## Deployment & Submission

- Push all changes to `main`
- Deploy full project on **Vercel** (frontend)
- Deploy backend on **Railway** or AWS/GCP
- Add deployed project link to README
- Record a video of the cloning tool in use and add link to README
- Click "complete assessment" in Candidate Code
