# 🛍️ AI Product Creative Generation Workflow

An end-to-end multi-agent system for ecommerce brands that automatically generates product marketing videos and images from a product page URL.

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    FastAPI Backend (Orchestrator)                │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    ┌──────────────────────────────────────┐   │
│  │  Single URL  │    │         CSV Bulk Upload              │   │
│  │   Input      │    │   (Async Queue + Job Tracking)       │   │
│  └──────┬───────┘    └──────────────┬───────────────────────┘   │
│         │                           │                            │
│         └──────────────┬────────────┘                           │
│                        ▼                                         │
│         ┌──────────────────────────┐                            │
│         │   LangGraph Orchestrator │                            │
│         │   (Agent State Machine)  │                            │
│         └──────────────┬───────────┘                            │
│                        │                                         │
│    ┌───────────────────┼─────────────────────┐                  │
│    ▼                   ▼                     ▼                   │
│ ┌──────────┐    ┌──────────────┐    ┌──────────────┐           │
│ │ Agent 1  │    │   Agent 2    │    │   Agent 3    │           │
│ │ Product  │───▶│  Creative    │───▶│   Prompt     │           │
│ │ Research │    │  Strategy    │    │  Generation  │           │
│ └──────────┘    └──────────────┘    └──────┬───────┘           │
│                                            │                     │
│                           ┌────────────────┴──────────┐         │
│                           ▼                           ▼         │
│                  ┌──────────────────┐    ┌─────────────────┐   │
│                  │    Agent 4       │    │    Agent 5      │   │
│                  │  Image Gen       │    │   Video Gen     │   │
│                  │  (5 images via   │    │  (2 videos via  │   │
│                  │  FLUX / SDXL)    │    │  CogVideoX)     │   │
│                  └────────┬─────────┘    └───────┬─────────┘   │
│                           │                      │              │
│                           └─────────┬────────────┘             │
│                                     ▼                           │
│                          ┌──────────────────────┐              │
│                          │      Agent 6         │              │
│                          │  Review / Critic     │              │
│                          │  (Quality + Retry)   │              │
│                          └──────────┬───────────┘              │
│                                     │                           │
│                          ┌──────────▼───────────┐              │
│                          │    Final Output       │              │
│                          │  (Images + Videos +   │              │
│                          │   Strategy Report)    │              │
│                          └──────────────────────┘              │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🤖 Agent Details

| Agent | Model | Purpose |
|-------|-------|---------|
| Product Research | `llama-3.3-70b` via Groq | Scrapes + understands product URL |
| Creative Strategy | `llama-3.3-70b` via Groq | Generates hooks, audiences, visual themes |
| Prompt Generation | `llama-3.3-70b` via Groq | Crafts optimized image/video prompts |
| Image Generation | `FLUX.1-schnell` via Together AI | Generates 5 product images |
| Video Generation | `CogVideoX-5b` via Replicate / `AnimateDiff` via HF | Generates 2 short videos |
| Review / Critic | `llama-3.3-70b` via Groq | Evaluates quality, flags issues, retries |

---

## 📦 Tech Stack

- **Orchestration**: LangGraph (state machine agent graphs)
- **LLM Inference**: Groq API (free tier) — `llama-3.3-70b-versatile`
- **Image Generation**: Together AI FLUX.1-schnell (free credits) + Stable Diffusion XL via HuggingFace
- **Video Generation**: CogVideoX-5b via Replicate OR AnimateDiff via local diffusers
- **Web Scraping**: `crawl4ai` + `BeautifulSoup4`
- **Backend API**: FastAPI + Celery + Redis (async job queue)
- **Frontend**: React + TailwindCSS
- **Storage**: Local filesystem (swap for S3 in prod)

---

## 🚀 Quick Start

### Prerequisites

```bash
python >= 3.10
node >= 18
redis-server
```

### 1. Clone & Install

> **Security:** Never commit `.env` or real API keys to GitHub.

**Where to put API keys:**
| How you run | Put keys in |
|-------------|-------------|
| Local (`uvicorn` / `celery` from `backend/`) | **`backend/.env`** (recommended) |
| Docker Compose | **Project root `.env`** (Compose reads this) |
| Both | Same keys in both files, or only root `.env` for Docker |

```bash
git clone https://github.com/YOUR_USERNAME/ai-product-creative-workflow
cd ai-product-creative-workflow

# Backend
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Frontend
cd ../frontend
npm install
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

Required keys (all have free tiers):

```env
GROQ_API_KEY=           # https://console.groq.com (free)
TOGETHER_API_KEY=       # https://api.together.xyz (free $25 credits)
REPLICATE_API_KEY=      # https://replicate.com (free credits)
HF_TOKEN=               # https://huggingface.co (free)

# Optional / provider-specific keys
STABILITY_API_KEY=      # https://platform.stability.ai (optional)
STABILITY_API_URL=      # model-specific endpoint if required
```

Additional runtime vars (local defaults shown):

```env
# Redis / Celery broker & backend
REDIS_URL=redis://localhost:6379/0

# Local output directory for generated assets
OUTPUT_DIR=./outputs

# Frontend: leave empty for Docker/nginx same-origin; use http://localhost:8000 for standalone Vite dev
VITE_API_URL=
```

A root-level `sample_products.csv` is included for bulk upload demos with
`brand_name`, `priority`, and `extra_instructions` columns.
```

### 3. Start Services

```bash
# Terminal 1: Redis (recommended: Docker Compose)
# Option A — run only Redis via Docker Compose (recommended):
docker compose up -d redis

# Option B — run Redis locally (if installed on host):
redis-server

# Terminal 2: Celery Worker (Linux/macOS/Docker only)
cd backend
celery -A api.celery_app worker --loglevel=info

# Windows: Celery prefork pool crashes — either skip Celery (API runs jobs inline)
# or use solo pool:  .\scripts\start-celery-windows.ps1
# Default on Windows: only Redis + uvicorn + frontend are required.
# Bulk CSV runs inline on Windows (background tasks) when Celery is disabled.

# Terminal 3: FastAPI
uvicorn api.main:app --reload --port 8000

# Terminal 4: Frontend
cd frontend
npm run dev
```

App runs at: **http://localhost:5173**

---

## ☁️ Deployment (Vercel + Render)

Minimal working deployment using a single Render service (API + Celery worker in one process) plus Redis.

### Frontend (Vercel)

1. Import the repo in Vercel.
2. Set Root Directory to `frontend`.
3. Build command: `npm run build` (default).
4. Output directory: `dist` (default).
5. Environment variable:

```env
VITE_API_URL=https://YOUR_RENDER_BACKEND.onrender.com
```

### Backend (Render)

1. Create a new Render Blueprint and select the repo.
2. Render will pick up [render.yaml](render.yaml) and provision:
  - A Python web service (FastAPI + Celery worker in one process)
  - A Redis service for queue + job status
3. Set required environment variables in the Render dashboard:

```env
GROQ_API_KEY=
TOGETHER_API_KEY=
REPLICATE_API_KEY=
HF_TOKEN=
# Optional
STABILITY_API_KEY=
STABILITY_API_URL=
```

4. Confirm the API is live:

```
GET https://YOUR_RENDER_BACKEND.onrender.com/health
```

Notes:
- Outputs are stored in a Render disk at `/var/data/outputs` and served from `/outputs/*`.
- Bulk CSV requires Celery; the Render start command runs a worker alongside the API for the demo.

---

## 🐳 Docker (Recommended)

```bash
docker-compose up --build
```

Then open **http://localhost:5173**

## Run only Redis

To start just the Redis service (no other containers):

```bash
docker compose up -d redis
docker compose ps
docker compose logs -f redis
```

If running the backend locally (not in Docker), keep `REDIS_URL=redis://localhost:6379/0` in `backend/.env` and ensure Redis is reachable on the host.

---

## 📋 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/generate` | Start single URL job |
| `GET` | `/api/jobs/{job_id}` | Poll job status |
| `POST` | `/api/bulk` | Upload CSV for bulk processing |
| `GET` | `/api/bulk/{batch_id}` | Get batch status |
| `GET` | `/api/results/{job_id}` | Get final results |

---

## Model fallback & rate limits

This project supports environment-driven LLM model selection and graceful handling
of provider rate limits. See `backend/README.md` for details and recommended
`GROQ_MODEL` / `GROQ_FALLBACK_MODEL` settings used by the agents.

When a provider returns rate-limited responses (HTTP 429 / RateLimitError), the
agents will attempt a configured fallback model where applicable and log
events like `agent2_rate_limited`, `agent4_rate_limited`, or `agent5_rate_limited`.
Some agents also use local fallbacks (image/video pipelines) to keep workflows
progressing when API quotas are exhausted.

### Example: Single URL

```bash
curl -X POST http://localhost:8000/api/generate \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.amazon.com/dp/B08N5WRWNW", "brand_name": "Amazon", "priority": "normal", "extra_instructions": "Focus on product durability"}'
```

### Example: CSV Bulk Upload

```bash
curl -X POST http://localhost:8000/api/bulk \
  -F "file=@products.csv"
```

CSV format:
```csv
url,brand_name,priority,extra_instructions
https://example.com/product-1,BrandA,high,Highlight premium materials
https://example.com/product-2,BrandB,normal,Emphasize everyday use
```

---

## 📁 Project Structure

```
ai-product-creative-workflow/
├── backend/
│   ├── agents/
│   │   ├── product_research.py      # Agent 1
│   │   ├── creative_strategy.py     # Agent 2
│   │   ├── prompt_generation.py     # Agent 3
│   │   ├── image_generation.py      # Agent 4
│   │   ├── video_generation.py      # Agent 5
│   │   └── review_critic.py         # Agent 6
│   ├── api/
│   │   ├── main.py                  # FastAPI app
│   │   ├── routes.py                # API routes
│   │   └── celery_app.py            # Async task queue
│   ├── models/
│   │   └── schemas.py               # Pydantic models
│   ├── utils/
│   │   ├── scraper.py               # Web scraping
│   │   └── storage.py               # File management
│   ├── graph.py                     # LangGraph orchestration
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   └── App.jsx
│   └── package.json
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## 🔄 Agent Flow (LangGraph State Machine)

```python
# Simplified state machine
START
  → product_research      # Extract title, features, pricing, reviews
  → creative_strategy     # Generate hooks, audiences, visual themes
  → prompt_generation     # Create image + video prompts
  → [image_gen, video_gen]  # Parallel execution
  → review_critic         # Quality check (loops back if fails)
  → END
```

Each agent node passes a shared `WorkflowState` object containing:
- `product_data` — scraped info
- `creative_strategy` — hooks, angles, themes
- `image_prompts` / `video_prompts` — generated prompts
- `generated_images` / `generated_videos` — output files
- `review_results` — quality scores and flags
- `retry_count` — loop guard

---

## 🧪 Evaluation Criteria for Review Agent

The Critic Agent scores each creative on:

| Dimension | Weight |
|-----------|--------|
| Brand consistency | 25% |
| Product accuracy (no hallucinations) | 30% |
| Visual quality | 20% |
| Marketing hook strength | 15% |
| CTA clarity | 10% |

If score < 0.7, prompts are automatically revised and regenerated (max 2 retries).

---

## 🔧 Alternative Free Inference Options

| Service | Models Available | Free Tier |
|---------|-----------------|-----------|
| Groq | Llama 3.3 70B, Mixtral | 14,400 req/day |
| Together AI | FLUX.1, SDXL, Llama | $25 free credits |
| Hugging Face | All open models | Free inference API |
| Replicate | CogVideoX, AnimateDiff | $5 free credits |
| OpenRouter | 100+ models | Free tier available |

---

## 📊 Sample Output

After processing a URL, you receive:
- `product_research.json` — structured product data
- `creative_strategy.json` — hooks, themes, messaging
- `image_01.png` through `image_05.png` — 5 marketing images
- `video_01.mp4` + `video_02.mp4` — 2 short marketing videos
- `review_report.json` — quality scores and notes

---

## 🤝 Contributing

PRs welcome. See `CONTRIBUTING.md` for guidelines.

## 📄 License

MIT