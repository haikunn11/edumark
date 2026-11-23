# OPERATIONS — Deploy, Runbook & Rollback

> **Mục đích:** Hướng dẫn vận hành, deploy và rollback hệ thống EduMark AI.
> **Tier:** 🟢 (Fast — tài liệu vận hành)

---

## 1. Môi Trường (Environments)

| Env         | Mục đích         | URL                      | Infra                     |
|:------------|:-----------------|:-------------------------|:--------------------------|
| `local`     | Dev + test       | `http://127.0.0.1:8000`  | Laptop dev, SQLite, InMemory cache |
| `staging`   | Pre-production   | TBD                      | VPS / Cloud Run, PostgreSQL, Redis |
| `production`| Teacher sử dụng  | TBD                      | Cloud Run / Railway, PostgreSQL, Redis, R2 |

---

## 2. Local Development — Quickstart

### 2.1. Setup

```bash
# 1. Clone repo
git clone <repo_url>
cd duanvoiemy

# 2. Python environment
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/Mac

# 3. Install dependencies
pip install fastapi uvicorn pydantic pillow opencv-python-headless \
            google-genai openpyxl PyMuPDF python-multipart

# 4. Environment variables
copy .env.example .env
# Chỉnh sửa .env:
#   GEMINI_API_KEY=your_key_here
#   ENVIRONMENT=local

# 5. Run server
uvicorn backend.app.main:app --reload --port 8000

# 6. Run tests
python run_test_flow.py
python -m pytest tests/ -v
```

### 2.2. Environment Variables

| Variable            | Mô tả                   | Mặc định          | Required |
|:--------------------|:-------------------------|:-------------------|:--------:|
| `GEMINI_API_KEY`    | Google AI Studio API key | —                  | ✅       |
| `ENVIRONMENT`       | local / staging / prod   | `local`            | ✅       |
| `BLUR_THRESHOLD`    | Ngưỡng blur detection    | `100.0`            | ❌       |
| `CACHE_TTL`         | Cache TTL (seconds)      | `86400`            | ❌       |
| `MAX_BATCH_SIZE`    | Tối đa bài/batch         | `50`               | ❌       |
| `REDIS_URL`         | Redis connection string  | — (InMemory nếu thiếu) | ❌   |
| `DATABASE_URL`      | PostgreSQL URL           | `sqlite:///data.db`| ❌       |
| `STORAGE_BACKEND`   | local / r2               | `local`            | ❌       |
| `R2_BUCKET`         | Cloudflare R2 bucket     | —                  | ❌       |

---

## 3. Staging / Production Deploy

### 3.1. Docker (Khuyến nghị)

```dockerfile
# Dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY run_test_flow.py .

EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
# Build & Run
docker build -t edumark-ai:latest .
docker run -p 8000:8000 --env-file .env edumark-ai:latest
```

### 3.2. Cloud Run (Google Cloud)

```bash
# Deploy
gcloud run deploy edumark-ai \
  --source . \
  --port 8000 \
  --memory 1Gi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 3 \
  --set-env-vars "ENVIRONMENT=production"
```

### 3.3. Railway / Render

```bash
# railway.toml hoặc render.yaml
# Start command: uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT
```

---

## 4. Runbook — Thao Tác Vận Hành Thường Ngày

### 4.1. Kiểm Tra Sức Khỏe Hệ Thống

```bash
curl http://localhost:8000/health
# Expected: {"status": "ok", "version": "0.1.0"}
```

### 4.2. Xóa Cache (khi cần force re-OCR)

```bash
# API endpoint (future)
curl -X POST http://localhost:8000/api/admin/cache/clear

# Hoặc restart server (InMemory cache sẽ mất)
```

### 4.3. Backup Data

```bash
# SQLite (local)
cp data.db data_backup_$(date +%Y%m%d).db

# PostgreSQL (staging/prod)
pg_dump $DATABASE_URL > backup_$(date +%Y%m%d).sql
```

### 4.4. Xem Logs

```bash
# Local: stdout
# Cloud Run: gcloud logging
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=edumark-ai" --limit 50
```

---

## 5. Rollback Procedure

### 5.1. Code Rollback

```bash
# 1. Tìm commit trước
git log --oneline -5

# 2. Revert
git revert HEAD

# 3. Redeploy
# Docker: docker build + docker run với tag cũ
# Cloud Run: gcloud run deploy --image gcr.io/.../edumark-ai:previous-tag
```

### 5.2. Database Rollback

```bash
# Restore backup
psql $DATABASE_URL < backup_YYYYMMDD.sql
```

### 5.3. Rollback Checklist

- [ ] Identify broken commit/deployment
- [ ] Revert code or deploy previous image
- [ ] Verify `/health` returns OK
- [ ] Test 1 submission grade end-to-end
- [ ] Notify teachers if service was down > 5 minutes

---

## 6. Monitoring (Future — Phase 2)

| Metric                       | Tool           | Alert Threshold          |
|:-----------------------------|:---------------|:-------------------------|
| API latency p95              | Cloud Monitoring | > 10s                   |
| Error rate (5xx)             | Cloud Monitoring | > 5% in 5 min           |
| Gemini API quota usage       | Google AI Studio | > 80% daily quota       |
| Disk/storage usage           | System metrics   | > 80% capacity          |
| Cache hit rate               | Custom metric    | < 30% (investigate)     |

---

## 7. Thứ Tự Deploy Bắt Buộc

1. ✅ Chạy tests locally (`pytest` + `run_test_flow.py`)
2. ✅ Build Docker image (nếu containerized)
3. ✅ Deploy staging, verify `/health`
4. ✅ Smoke test: upload 1 ảnh → grade → review → approve
5. ✅ Deploy production
6. ✅ Smoke test production
7. ✅ Cập nhật `brain4agent/changelog.md` + tăng version
