from __future__ import annotations

import functools
import json
import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import psutil
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from flask import (
    Flask, abort, jsonify, redirect, render_template, request, session, url_for
)
from flask_wtf import CSRFProtect
from flask_wtf.csrf import generate_csrf
from werkzeug.middleware.proxy_fix import ProxyFix

from core import (
    AUDIT_PATH, COLLECTIONS, DATASET_MANIFESTS, DATA_DIR, AsyncLoop, AsyncStore,
    CQSE, GemmaIntegrityManager, Hyperfield, LocalContriever, OpenAIEmbedding,
    QuantumEngine, ResponsesReasoner, Runtime, WeaviateMemory, safe_json,
    simulate_dataset, stable_hash, utc_now,
)

APP_VERSION = "Web-1.0"


def read_secret(name: str, default: str = "") -> str:
    file_var = os.getenv(f"{name}_FILE", "").strip()
    if file_var:
        p = Path(file_var)
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    return os.getenv(name, default).strip()


def ensure_runtime_secret() -> str:
    supplied = read_secret("QNH_SECRET_KEY")
    if supplied:
        return supplied
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / ".flask-secret"
    if not path.exists():
        path.write_text(secrets.token_urlsafe(64), encoding="utf-8")
        os.chmod(path, 0o600)
    return path.read_text(encoding="utf-8").strip()


app = Flask(__name__, static_folder="static", template_folder="templates")
app.config.update(
    SECRET_KEY=ensure_runtime_secret(),
    MAX_CONTENT_LENGTH=int(os.getenv("QNH_MAX_BODY_BYTES", str(2 * 1024 * 1024))),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=os.getenv("QNH_COOKIE_SECURE", "0") == "1",
    PERMANENT_SESSION_LIFETIME=3600,
    WTF_CSRF_TIME_LIMIT=3600,
)

if os.getenv("QNH_TRUST_PROXY", "0") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

csrf = CSRFProtect(app)
ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)

loop = AsyncLoop()
store = AsyncStore()
quantum = QuantumEngine()
cqse = CQSE()
hyperfield = Hyperfield()

def openai_key():
    return read_secret("OPENAI_API_KEY")

def weaviate_url():
    return os.getenv("WEAVIATE_URL", "http://weaviate:8080").strip()

def weaviate_key():
    return read_secret("WEAVIATE_API_KEY")

embedder = OpenAIEmbedding(openai_key)
contriever = LocalContriever()
gemma = GemmaIntegrityManager()
reasoner = ResponsesReasoner(openai_key)
memory = WeaviateMemory(weaviate_url, weaviate_key, embedder, cqse, contriever)
runtime = Runtime(loop, store, quantum, cqse, hyperfield, memory, contriever, gemma, reasoner)
loop.run(store.init(), timeout=30)

LOGIN_BUCKETS: dict[str, deque] = defaultdict(deque)
API_BUCKETS: dict[str, deque] = defaultdict(deque)


def limited(bucket, key: str, limit: int, window: int) -> bool:
    now = time.time()
    q = bucket[key]
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def user_ip():
    return request.remote_addr or "unknown"


def authenticated():
    return bool(session.get("authenticated"))


def login_required(fn):
    @functools.wraps(fn)
    def inner(*args, **kwargs):
        if not authenticated():
            if request.path.startswith("/api/"):
                return jsonify({"error": "authentication required"}), 401
            return redirect(url_for("login", next=request.path))
        if request.path.startswith("/api/") and limited(API_BUCKETS, user_ip(), 180, 60):
            return jsonify({"error": "rate limit exceeded"}), 429
        return fn(*args, **kwargs)
    return inner


def admin_username():
    return os.getenv("QNH_ADMIN_USER", "admin")


def verify_password(password: str) -> bool:
    expected_hash = read_secret("QNH_ADMIN_PASSWORD_HASH")
    bootstrap = read_secret("QNH_BOOTSTRAP_ADMIN_PASSWORD")
    if expected_hash:
        try:
            return ph.verify(expected_hash, password)
        except VerifyMismatchError:
            return False
        except Exception:
            return False
    if bootstrap:
        return secrets.compare_digest(bootstrap, password)
    return False


@app.after_request
def security_headers(response):
    nonce = getattr(request, "_csp_nonce", None)
    csp = (
        "default-src 'self'; "
        "base-uri 'none'; frame-ancestors 'none'; object-src 'none'; "
        "form-action 'self'; img-src 'self' data:; font-src 'self'; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "connect-src 'self'; worker-src 'none'; manifest-src 'self'"
    )
    response.headers["Content-Security-Policy"] = csp
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=(), "
        "bluetooth=(), magnetometer=(), gyroscope=(), accelerometer=()"
    )
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    if os.getenv("QNH_HSTS", "0") == "1":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok", "version": APP_VERSION, "time": utc_now()})


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if limited(LOGIN_BUCKETS, user_ip(), 8, 300):
            abort(429)
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        ok = secrets.compare_digest(username, admin_username()) and verify_password(password)
        if ok:
            session.clear()
            session["authenticated"] = True
            session["username"] = username
            session.permanent = True
            loop.run(store.append_audit(username, "login_success", {"ip_hash": stable_hash(user_ip())[:16]}))
            return redirect(url_for("index"))
        loop.run(store.append_audit(username or "unknown", "login_failure", {"ip_hash": stable_hash(user_ip())[:16]}))
        return render_template("login.html", error="Invalid credentials.", csrf_token=generate_csrf()), 401
    return render_template("login.html", error=None, csrf_token=generate_csrf())


@app.post("/logout")
@login_required
def logout():
    actor = session.get("username", "unknown")
    loop.run(store.append_audit(actor, "logout", {}))
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def index():
    return render_template(
        "index.html",
        version=APP_VERSION,
        username=session.get("username", "operator"),
        csrf_token=generate_csrf(),
        collection_count=len(COLLECTIONS),
        datasets=DATASET_MANIFESTS,
    )


@app.get("/api/bootstrap")
@login_required
def api_bootstrap():
    rows = loop.run(store.metrics(300))
    if not rows:
        loop.run(store.seed_demo(240))
        rows = loop.run(store.metrics(300))
    analytics = quantum.analyze(rows)
    status = {}
    try:
        status = loop.run(memory.status(), timeout=12)
    except Exception as exc:
        status = {"ready": False, "error": str(exc)}
    return jsonify({
        "version": APP_VERSION,
        "analytics": analytics,
        "weaviate": status,
        "datasets": [
            {"dataset_id": x[0], "name": x[1], "category": x[2]}
            for x in DATASET_MANIFESTS
        ],
        "security": {
            "csrf": True,
            "secure_cookie": app.config["SESSION_COOKIE_SECURE"],
            "hsts": os.getenv("QNH_HSTS", "0") == "1",
            "max_body_bytes": app.config["MAX_CONTENT_LENGTH"],
            "openai_configured": bool(openai_key()),
            "reasoner_configured": reasoner.configured(),
            "reasoning_model": reasoner.model or None,
            "contriever_enabled": contriever.enabled,
            "contriever_dependencies": contriever.dependencies_available(),
            "weaviate_auth_configured": bool(weaviate_key()),
        },
        "gemma": loop.run(gemma.verify(), timeout=60),
    })


@app.get("/api/telemetry")
@login_required
def api_telemetry():
    limit = max(20, min(int(request.args.get("limit", "300")), 2000))
    rows = loop.run(store.metrics(limit))
    return jsonify({"rows": rows, "analytics": quantum.analyze(rows) if rows else None})


@app.post("/api/demo/seed")
@login_required
def api_seed_demo():
    loop.run(store.seed_demo(240))
    loop.run(store.append_audit(session["username"], "demo_seed", {"count": 240}))
    return jsonify({"ok": True})


@app.post("/api/cqse")
@login_required
def api_cqse():
    payload = request.get_json(silent=True) or {}
    probe = str(payload.get("text") or "").strip()
    rows = loop.run(store.metrics(300))
    if not rows:
        return jsonify({"error": "no telemetry"}), 409
    analytics = quantum.analyze(rows)
    if not probe:
        c = analytics["current"]
        probe = (
            f"network telemetry ping={c['ping']} jitter={c['jitter']} "
            f"download={c['download_speed']} upload={c['upload_speed']} "
            f"anomalies={analytics['anomalies']}"
        )
    surface = cqse.encode(probe[:16000], analytics["q16"])
    if payload.get("store", True):
        props = {
            "surface_id": str(__import__("uuid").uuid4()),
            "created_at": utc_now(),
            "source_kind": "web-cqse",
            "summary": probe[:900],
            "palette_json": safe_json(surface["palette"]),
            "colorspace_json": safe_json({"rgb": surface["rgb"], "oklab": surface["oklab"], "oklch": surface["oklch"]}),
            "entropy_json": safe_json({"semantic_entropy": surface["semantic_entropy"], "q32_entropy": surface["q32_entropy"]}),
            "quantum_json": safe_json({"q16": analytics["q16"], "q32": surface["chromatic_quantum_32"]}),
            "experimental_notice": surface["experimental_notice"],
            "tags": ["web", "cqse", "experimental"],
        }
        try:
            loop.run(memory.insert("QNChromaticSurfaceMemoryV1", props, analytics["q16"]), timeout=90)
        except Exception as exc:
            surface["memory_warning"] = str(exc)
    loop.run(store.append_audit(session["username"], "cqse_generate", {"text_hash": stable_hash(probe)[:20]}))
    return jsonify({"probe": probe, "surface": surface, "analytics": analytics})


@app.post("/api/hyperfield")
@login_required
def api_hyperfield():
    payload = request.get_json(silent=True) or {}
    limit = max(20, min(int(payload.get("limit", 240)), 600))
    rows = loop.run(store.metrics(limit))
    if len(rows) < 2:
        return jsonify({"error": "insufficient telemetry"}), 409
    field = hyperfield.build(rows, quantum, cqse)
    loop.run(store.append_audit(session["username"], "hyperfield_build", {
        "points": field["point_count"],
        "regimes": len(field["regimes"]),
        "boundaries": len(field["boundaries"]),
    }))
    return jsonify(field)


@app.get("/api/memory/status")
@login_required
def api_memory_status():
    try:
        return jsonify(loop.run(memory.status(), timeout=12))
    except Exception as exc:
        return jsonify({"ready": False, "error": str(exc)}), 503


@app.post("/api/memory/setup")
@login_required
def api_memory_setup():
    try:
        result = loop.run(memory.setup(), timeout=60)
        loop.run(store.append_audit(session["username"], "weaviate_setup", {"collections": result}))
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 503


@app.post("/api/memory/search")
@login_required
def api_memory_search():
    payload = request.get_json(silent=True) or {}
    query = str(payload.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query required"}), 400
    try:
        hits = loop.run(memory.search(query[:8000], max(1, min(int(payload.get("limit", 12)), 30))), timeout=90)
        return jsonify({"query": query, "hits": hits})
    except Exception as exc:
        return jsonify({"error": str(exc), "hits": []}), 503


@app.post("/api/datasets/simulate")
@login_required
def api_dataset_simulate():
    payload = request.get_json(silent=True) or {}
    dataset_id = str(payload.get("dataset_id") or "")
    count = max(1, min(int(payload.get("count", 180)), 1000))
    try:
        records = simulate_dataset(dataset_id, count)
    except KeyError:
        return jsonify({"error": "unknown dataset"}), 404
    inserted = 0
    warnings = []
    for record in records:
        try:
            loop.run(memory.insert("QNDatasetCorpusMemoryV1", record, None, use_frontier_embeddings=False), timeout=45)
            inserted += 1
        except Exception as exc:
            warnings.append(str(exc))
            break
    loop.run(store.append_audit(session["username"], "dataset_simulate", {
        "dataset_id": dataset_id, "generated": len(records), "weaviate_inserted": inserted,
    }))
    return jsonify({
        "dataset_id": dataset_id,
        "generated": len(records),
        "weaviate_inserted": inserted,
        "label_counts": {
            label: sum(1 for r in records if r["label"] == label)
            for label in sorted({r["label"] for r in records})
        },
        "warnings": warnings[:3],
    })


@app.get("/api/actions")
@login_required
def api_actions():
    return jsonify({"actions": loop.run(store.actions())})


@app.post("/api/actions")
@login_required
def api_action_create():
    payload = request.get_json(silent=True) or {}
    title = str(payload.get("title") or "").strip()[:160]
    description = str(payload.get("description") or "").strip()[:4000]
    if not title or not description:
        return jsonify({"error": "title and description required"}), 400
    action_id = loop.run(store.create_action(
        title, description,
        str(payload.get("priority") or "medium"),
        str(payload.get("risk") or "medium"),
    ))
    loop.run(store.append_audit(session["username"], "action_create", {"action_id": action_id}))
    return jsonify({"action_id": action_id}), 201


@app.post("/api/actions/<action_id>/<state>")
@login_required
def api_action_state(action_id, state):
    if state not in {"approved", "rejected", "pending", "completed", "failed"}:
        return jsonify({"error": "invalid state"}), 400
    loop.run(store.set_action_state(action_id, state))
    loop.run(store.append_audit(session["username"], "action_state", {"action_id": action_id, "state": state}))
    return jsonify({"ok": True})


@app.get("/api/models")
@login_required
def api_models():
    return jsonify({
        "openai_embeddings": {
            "configured": bool(openai_key()),
            "model": os.getenv("QNH_OPENAI_EMBED_MODEL", "text-embedding-3-large"),
        },
        "reasoner": {
            "configured": reasoner.configured(),
            "model": reasoner.model or None,
        },
        "contriever": {
            "enabled": contriever.enabled,
            "dependencies_available": contriever.dependencies_available(),
            "model": os.getenv("QNH_FB_MODEL", "facebook/contriever-msmarco"),
            "revision": os.getenv("QNH_FB_REVISION", "287db3b2d7662e688c051c351a0739de4ef2d6d7"),
            "safetensors_only": True,
        },
        "gemma": loop.run(gemma.verify(), timeout=60),
    })


@app.post("/api/models/gemma/download")
@login_required
def api_gemma_download():
    try:
        result = loop.run(gemma.download(), timeout=7200)
        loop.run(store.append_audit(session["username"], "gemma_download_verified", {
            "sha256": result.get("actual_sha256"),
            "bytes": result.get("bytes"),
        }))
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 403 if "disabled" in str(exc).lower() else 500


@app.post("/api/analyze")
@login_required
def api_analyze():
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode") or "Deep Network Analysis")[:120]
    rows = loop.run(store.metrics(max(30, min(int(payload.get("limit", 260)), 600))))
    if not rows:
        return jsonify({"error": "no telemetry"}), 409
    analytics = quantum.analyze(rows)
    current = analytics["current"]
    probe = (
        f"{mode}; ping={current['ping']} jitter={current['jitter']} "
        f"download={current['download_speed']} upload={current['upload_speed']} "
        f"anomalies={analytics['anomalies']}"
    )
    surface = cqse.encode(probe, analytics["q16"])
    field = hyperfield.build(rows, quantum, cqse) if len(rows) >= 2 else None
    memories = []
    try:
        memories = loop.run(memory.search(probe, 10), timeout=90)
    except Exception:
        pass
    try:
        result = loop.run(
            reasoner.analyze(mode, analytics, surface, field, memories),
            timeout=180,
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 503
    loop.run(store.append_audit(session["username"], "structured_analysis", {
        "mode": mode, "model": result.get("model"), "response_id": result.get("response_id"),
    }))
    return jsonify({
        "mode": mode,
        "result": result,
        "memory_count": len(memories),
    })


@app.get("/api/system")
@login_required
def api_system():
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(str(DATA_DIR if DATA_DIR.exists() else "/"))
    container = {
        "pid": os.getpid(),
        "uid": os.getuid() if hasattr(os, "getuid") else None,
        "read_only_expected": os.getenv("QNH_CONTAINER_READ_ONLY", "1") == "1",
        "data_dir": str(DATA_DIR),
    }
    return jsonify({
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory_percent": vm.percent,
        "memory_available": vm.available,
        "disk_percent": disk.percent,
        "container": container,
        "models_dir": os.getenv("QNH_MODELS_DIR", str(DATA_DIR / "models")),
        "openai_configured": bool(openai_key()),
        "reasoner_configured": reasoner.configured(),
        "reasoning_model": reasoner.model or None,
        "contriever_enabled": contriever.enabled,
        "contriever_dependencies": contriever.dependencies_available(),
        "gemma": loop.run(gemma.verify(), timeout=60),
        "weaviate_url": weaviate_url(),
        "weaviate_key_configured": bool(weaviate_key()),
    })


@app.errorhandler(413)
def too_large(_):
    return jsonify({"error": "request body too large"}), 413


@app.errorhandler(429)
def too_many(_):
    return jsonify({"error": "rate limit exceeded"}), 429


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "8080")), debug=False)
