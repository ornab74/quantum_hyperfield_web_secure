from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import random
import re
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import aiosqlite
import httpx
import numpy as np
import pennylane as qml

try:
    import weaviate
    from weaviate.classes.config import Configure, DataType, Property
    from weaviate.classes.init import Auth
    from weaviate.classes.query import MetadataQuery
    WEAVIATE_OK = True
except Exception:
    weaviate = None
    Configure = DataType = Property = Auth = MetadataQuery = None
    WEAVIATE_OK = False

DATA_DIR = Path(os.getenv("QNH_DATA_DIR", "/var/data"))
DB_PATH = Path(os.getenv("QNH_DB_PATH", str(DATA_DIR / "hyperfield.db")))
AUDIT_PATH = Path(os.getenv("QNH_AUDIT_PATH", str(DATA_DIR / "audit-chain.jsonl")))
MODEL_DIR = Path(os.getenv("QNH_MODELS_DIR", str(DATA_DIR / "models")))

OPENAI_EMBED_MODEL = os.getenv("QNH_OPENAI_EMBED_MODEL", "text-embedding-3-large")
OPENAI_EMBED_DIM = int(os.getenv("QNH_OPENAI_EMBED_DIM", "3072"))
OPENAI_EMBED_URL = "https://api.openai.com/v1/embeddings"

VEC_OPENAI = "openai_3072"
VEC_FB = "fb_contriever_768"
FB_MODEL = os.getenv("QNH_FB_MODEL", "facebook/contriever-msmarco")
FB_REVISION = os.getenv(
    "QNH_FB_REVISION",
    "287db3b2d7662e688c051c351a0739de4ef2d6d7",
)
FB_SAFETENSORS_SHA256 = "185c2c743bbb9ec15727575a931dd95fdf035e1fc377dd846a9a4f9fa89dd54b"
FB_DIM = 768

GEMMA_MODEL_NAME = "gemma-4-E2B-it.litertlm"
GEMMA_SHA256 = os.getenv(
    "QNH_GEMMA_SHA256",
    "ab7838cdfc8f77e54d8ca45eadceb20452d9f01e4bfade03e5dce27911b27e42",
)
GEMMA_MODEL_URL = os.getenv(
    "QNH_GEMMA_MODEL_URL",
    "https://huggingface.co/multimodel/gemma-4-E2B-it-litert-lm/resolve/main/"
    "gemma-4-E2B-it.litertlm?download=true",
)
GEMMA_MODEL_PATH = MODEL_DIR / GEMMA_MODEL_NAME
VEC_Q16 = "quantum_16"
VEC_CQ96 = "chromatic_surface_96"
VEC_CQQ32 = "chromatic_quantum_32"

COLLECTIONS = {
    "QNTelemetryMemoryV1": {
        "quantum": True,
        "chromatic": True,
        "properties": [
            ("event_id", "TEXT"), ("timestamp", "TEXT"), ("summary", "TEXT"),
            ("ping_ms", "NUMBER"), ("jitter_ms", "NUMBER"),
            ("download_mbps", "NUMBER"), ("upload_mbps", "NUMBER"),
            ("anomaly_count", "INT"), ("health_state", "TEXT"),
            ("quantum_json", "TEXT"), ("tags", "TEXT_ARRAY"),
        ],
    },
    "QNIncidentMemoryV1": {
        "quantum": False,
        "chromatic": True,
        "properties": [
            ("incident_id", "TEXT"), ("opened_at", "TEXT"), ("severity", "TEXT"),
            ("title", "TEXT"), ("evidence", "TEXT"), ("hypothesis", "TEXT"),
            ("status", "TEXT"), ("resolution", "TEXT"), ("tags", "TEXT_ARRAY"),
        ],
    },
    "QNActionPlaybookMemoryV1": {
        "quantum": False,
        "chromatic": True,
        "properties": [
            ("action_id", "TEXT"), ("created_at", "TEXT"), ("title", "TEXT"),
            ("priority", "TEXT"), ("risk", "TEXT"), ("description", "TEXT"),
            ("approval_state", "TEXT"), ("outcome", "TEXT"), ("tags", "TEXT_ARRAY"),
        ],
    },
    "QNDatasetCorpusMemoryV1": {
        "quantum": False,
        "chromatic": True,
        "properties": [
            ("record_id", "TEXT"), ("dataset_id", "TEXT"), ("dataset_name", "TEXT"),
            ("simulated", "BOOL"), ("label", "TEXT"), ("category", "TEXT"),
            ("summary", "TEXT"), ("features_json", "TEXT"),
            ("ingested_at", "TEXT"), ("tags", "TEXT_ARRAY"),
        ],
    },
    "QNChromaticSurfaceMemoryV1": {
        "quantum": True,
        "chromatic": True,
        "properties": [
            ("surface_id", "TEXT"), ("created_at", "TEXT"), ("source_kind", "TEXT"),
            ("summary", "TEXT"), ("palette_json", "TEXT"),
            ("colorspace_json", "TEXT"), ("entropy_json", "TEXT"),
            ("quantum_json", "TEXT"), ("experimental_notice", "TEXT"),
            ("tags", "TEXT_ARRAY"),
        ],
    },
    "QNTemporalHyperfieldMemoryV1": {
        "quantum": True,
        "chromatic": True,
        "properties": [
            ("worldline_id", "TEXT"), ("created_at", "TEXT"), ("summary", "TEXT"),
            ("point_count", "INT"), ("regime_count", "INT"),
            ("boundary_count", "INT"), ("mean_velocity", "NUMBER"),
            ("mean_curvature", "NUMBER"), ("mean_quantum_entropy", "NUMBER"),
            ("mean_quantum_js", "NUMBER"), ("max_anomaly_score", "NUMBER"),
            ("regimes_json", "TEXT"), ("boundaries_json", "TEXT"),
            ("experimental_notice", "TEXT"), ("tags", "TEXT_ARRAY"),
        ],
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_hash(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8", "replace"))
        h.update(b"\0")
    return h.hexdigest()


def safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def l2(values: Iterable[float]) -> list[float]:
    arr = np.asarray(list(values), dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm > 0:
        arr /= norm
    return arr.tolist()


class AsyncLoop:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._worker, daemon=True, name="qnh-async")
        self.thread.start()

    def _worker(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro, timeout: float = 120.0):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result(timeout=timeout)


class AsyncStore:
    def __init__(self, path: Path = DB_PATH):
        self.path = path

    @asynccontextmanager
    async def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self.path)
        try:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("PRAGMA busy_timeout=5000")
            yield db
        finally:
            await db.close()

    async def init(self):
        async with self.connection() as db:
            await db.executescript("""
            CREATE TABLE IF NOT EXISTS network_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ping REAL NOT NULL,
                jitter REAL NOT NULL,
                download_speed REAL NOT NULL,
                upload_speed REAL NOT NULL,
                timestamp TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_network_logs_timestamp
            ON network_logs(timestamp);

            CREATE TABLE IF NOT EXISTS action_queue (
                action_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                title TEXT NOT NULL,
                priority TEXT NOT NULL,
                risk TEXT NOT NULL,
                description TEXT NOT NULL,
                approval_state TEXT NOT NULL DEFAULT 'pending',
                outcome TEXT
            );

            CREATE TABLE IF NOT EXISTS audit_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                actor TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS dataset_records (
                record_id TEXT PRIMARY KEY,
                dataset_id TEXT NOT NULL,
                dataset_name TEXT NOT NULL,
                simulated INTEGER NOT NULL,
                label TEXT,
                category TEXT,
                features_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """)
            await db.commit()

    async def metrics(self, limit: int = 300) -> list[dict[str, Any]]:
        async with self.connection() as db:
            cur = await db.execute("""
                SELECT ping,jitter,download_speed,upload_speed,timestamp
                FROM network_logs ORDER BY timestamp DESC LIMIT ?
            """, (max(2, min(limit, 5000)),))
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def seed_demo(self, count: int = 240):
        rng = random.Random(0xC051A)
        now = time.time()
        rows = []
        for i in range(count):
            phase = i / 18.0
            anomaly = 1.0
            if 110 <= i <= 128:
                anomaly = 2.7
            ping = max(1, 24 + 6 * math.sin(phase) + rng.gauss(0, 1.7)) * anomaly
            jitter = max(0.1, 3.0 + 1.4 * math.sin(phase * 1.7) + rng.gauss(0, .5)) * anomaly
            download = max(2, 240 + 35 * math.cos(phase * .6) + rng.gauss(0, 8)) / max(anomaly, 1)
            upload = max(1, 64 + 14 * math.sin(phase * .4) + rng.gauss(0, 4)) / max(anomaly, 1)
            stamp = datetime.fromtimestamp(now - (count - i) * 60, timezone.utc).isoformat()
            rows.append((ping, jitter, download, upload, stamp))
        async with self.connection() as db:
            await db.executemany("""
                INSERT INTO network_logs(ping,jitter,download_speed,upload_speed,timestamp)
                VALUES(?,?,?,?,?)
            """, rows)
            await db.commit()

    async def actions(self) -> list[dict[str, Any]]:
        async with self.connection() as db:
            cur = await db.execute("""
                SELECT action_id,created_at,title,priority,risk,description,
                       approval_state,outcome
                FROM action_queue ORDER BY created_at DESC LIMIT 300
            """)
            return [dict(r) for r in await cur.fetchall()]

    async def create_action(self, title: str, description: str, priority="medium", risk="medium"):
        action_id = str(uuid.uuid4())
        async with self.connection() as db:
            await db.execute("""
                INSERT INTO action_queue(action_id,created_at,title,priority,risk,description)
                VALUES(?,?,?,?,?,?)
            """, (action_id, utc_now(), title, priority, risk, description))
            await db.commit()
        return action_id

    async def set_action_state(self, action_id: str, state: str):
        if state not in {"approved", "rejected", "pending", "completed", "failed"}:
            raise ValueError("invalid action state")
        async with self.connection() as db:
            await db.execute(
                "UPDATE action_queue SET approval_state=? WHERE action_id=?",
                (state, action_id),
            )
            await db.commit()

    async def append_audit(self, actor: str, event_type: str, payload: dict[str, Any]):
        async with self.connection() as db:
            cur = await db.execute("SELECT event_hash FROM audit_events ORDER BY seq DESC LIMIT 1")
            row = await cur.fetchone()
            prev = row["event_hash"] if row else "GENESIS"
            payload_json = safe_json(payload)
            event_hash = stable_hash(prev, actor, event_type, payload_json, utc_now(), str(uuid.uuid4()))
            await db.execute("""
                INSERT INTO audit_events(created_at,actor,event_type,payload_json,previous_hash,event_hash)
                VALUES(?,?,?,?,?,?)
            """, (utc_now(), actor, event_type, payload_json, prev, event_hash))
            await db.commit()
            return event_hash


class QuantumEngine:
    METRICS = ("ping", "jitter", "download_speed", "upload_speed")

    @staticmethod
    def robust_z(v: float, arr: np.ndarray) -> float:
        med = float(np.median(arr))
        mad = float(np.median(np.abs(arr - med)))
        if mad < 1e-9:
            std = float(np.std(arr))
            return 0.0 if std < 1e-9 else (v - med) / std
        return 0.67448975 * (v - med) / mad

    def q16(self, row: dict[str, Any], history: list[dict[str, Any]]) -> list[float]:
        norm = []
        for metric in self.METRICS:
            arr = np.asarray([float(r[metric]) for r in history], dtype=float)
            lo, hi = np.percentile(arr, [5, 95])
            if hi - lo < 1e-9:
                hi = lo + max(abs(lo) * .25, 1.0)
            norm.append(float(np.clip((float(row[metric]) - lo) / (hi - lo), 0, 1)))
        dev = qml.device("default.qubit", wires=4)

        @qml.qnode(dev)
        def circuit():
            for wire, value in enumerate(norm):
                qml.RY(math.pi * value, wires=wire)
                qml.RZ(math.pi * (value - .5), wires=wire)
            qml.CNOT(wires=[0,1]); qml.CNOT(wires=[1,2])
            qml.CNOT(wires=[2,3]); qml.CNOT(wires=[3,0])
            return qml.probs(wires=[0,1,2,3])
        return np.asarray(circuit(), dtype=np.float32).tolist()

    def analyze(self, history: list[dict[str, Any]]) -> dict[str, Any]:
        if not history:
            raise RuntimeError("No telemetry rows")
        current = history[0]
        metrics = {}
        anomalies = []
        for metric in self.METRICS:
            arr = np.asarray([float(r[metric]) for r in history], dtype=float)
            rz = self.robust_z(float(current[metric]), arr)
            chronological = arr[::-1]
            slope = float(np.polyfit(np.arange(len(chronological)), chronological, 1)[0]) if len(arr) > 2 else 0.0
            metrics[metric] = {
                "current": float(current[metric]),
                "median": float(np.median(arr)),
                "p05": float(np.percentile(arr, 5)),
                "p95": float(np.percentile(arr, 95)),
                "robust_z": rz,
                "slope": slope,
            }
            if abs(rz) >= 3.5:
                anomalies.append({"metric": metric, "robust_z": rz, "value": float(current[metric])})
        q = self.q16(current, history)
        entropy = float(-np.sum(np.asarray(q) * np.log2(np.asarray(q) + 1e-12)))
        return {
            "generated_at": utc_now(),
            "sample_count": len(history),
            "current": current,
            "metrics": metrics,
            "anomalies": anomalies,
            "q16": q,
            "q16_entropy": entropy,
        }


class CQSE:
    def __init__(self):
        self._proj = {}

    @staticmethod
    def hash_vector(text: str, dim=512) -> np.ndarray:
        v = np.zeros(dim, dtype=float)
        t = re.sub(r"\s+", " ", text.lower()).strip()
        for n in (2,3,4,5):
            for i in range(max(0, len(t)-n+1)):
                gram = t[i:i+n]
                h = int.from_bytes(hashlib.blake2b(gram.encode(), digest_size=8).digest(), "big")
                v[h % dim] += -1 if (h >> 8) & 1 else 1
        norm = np.linalg.norm(v)
        return v / norm if norm else v

    @staticmethod
    def srgb_linear(c):
        return c / 12.92 if c <= .04045 else ((c + .055)/1.055) ** 2.4

    @classmethod
    def rgb_xyz(cls, rgb):
        r,g,b = [cls.srgb_linear(float(np.clip(c,0,1))) for c in rgb]
        return (
            .4124564*r + .3575761*g + .1804375*b,
            .2126729*r + .7151522*g + .0721750*b,
            .0193339*r + .1191920*g + .9503041*b,
        )

    @staticmethod
    def xyz_oklab(x,y,z):
        l = .8189330101*x + .3618667424*y - .1288597137*z
        m = .0329845436*x + .9293118715*y + .0361456387*z
        s = .0482003018*x + .2643662691*y + .6338517070*z
        l,m,s = np.cbrt([l,m,s])
        return (
            float(.2104542553*l + .7936177850*m - .0040720468*s),
            float(1.9779984951*l - 2.4285922050*m + .4505937099*s),
            float(.0259040371*l + .7827717662*m - .8086757660*s),
        )

    @staticmethod
    def oklch(lab):
        L,a,b = lab
        C = math.sqrt(a*a+b*b)
        H = (math.atan2(b,a)/(2*math.pi)) % 1.0
        return L,C,H

    def encode(self, text: str, q16: Optional[list[float]] = None) -> dict[str, Any]:
        base = self.hash_vector(text)
        rng = np.random.default_rng(int(stable_hash("cqse", str(len(base)))[:8],16))
        proj = rng.normal(size=(12,len(base)))
        proj /= np.linalg.norm(proj,axis=1,keepdims=True)+1e-12
        channels = 1/(1+np.exp(-np.clip(proj@base,-18,18)))
        rgbs = [tuple(map(float,channels[i:i+3])) for i in range(0,12,3)]
        xyz = [self.rgb_xyz(x) for x in rgbs]
        labs = [self.xyz_oklab(*x) for x in xyz]
        lchs = [self.oklch(x) for x in labs]
        p = np.abs(base); p /= p.sum()+1e-12
        entropy = float(-np.sum(p*np.log2(p+1e-12)))
        seed = np.asarray([v for triple in rgbs+xyz+labs+lchs for v in triple] +
                          [entropy/(math.log2(len(base))+1e-12)] +
                          (q16 if q16 and len(q16)==16 else [0.0]*16), dtype=float)
        rng2 = np.random.default_rng(0xC051A)
        m = rng2.normal(size=(96,len(seed)))
        m /= np.linalg.norm(m,axis=1,keepdims=True)+1e-12
        c96 = np.tanh(m@seed)
        c96 = np.asarray(l2(c96))
        q32 = self._q32(c96, labs, lchs, entropy)
        palette = ["#{:02X}{:02X}{:02X}".format(
            *[max(0,min(255,int(round(c*255)))) for c in rgb]
        ) for rgb in rgbs]
        return {
            "base_source": "hashed_ngram_512",
            "palette": palette,
            "rgb": rgbs, "xyz": xyz, "oklab": labs, "oklch": lchs,
            "semantic_entropy": entropy,
            "chromatic_surface_96": c96.astype(np.float32).tolist(),
            "chromatic_quantum_32": q32,
            "q32_entropy": float(-np.sum(np.asarray(q32)*np.log2(np.asarray(q32)+1e-12))),
            "experimental_notice": "CQSE is an experimental retrieval surface, not a validated embedding standard or evidence of quantum advantage.",
        }

    @staticmethod
    def _q32(c96, labs, lchs, entropy):
        dev = qml.device("default.qubit", wires=5)
        anchors = []
        for lab,lch in zip(labs,lchs):
            anchors.extend([lab[0], math.tanh(lab[1]), math.tanh(lab[2]), lch[1], lch[2]])
        anchors.append(math.tanh(entropy/9))
        angles = [
            math.pi * float(np.clip(.5 + .23*anchors[i%len(anchors)] + .27*c96[(i*7)%96], 0, 1))
            for i in range(15)
        ]
        @qml.qnode(dev)
        def circuit():
            k=0
            for layer in range(3):
                for wire in range(5):
                    qml.RY(angles[k],wires=wire)
                    qml.RZ((angles[k]-math.pi/2)/(layer+1),wires=wire)
                    k += 1
                for a,b in ((0,1),(1,2),(2,3),(3,4),(4,0)):
                    qml.CNOT(wires=[a,b])
            return qml.probs(wires=range(5))
        return np.asarray(circuit(),dtype=np.float32).tolist()


class Hyperfield:
    @staticmethod
    def js(p,q):
        p=np.asarray(p,dtype=float); q=np.asarray(q,dtype=float)
        p/=p.sum()+1e-12; q/=q.sum()+1e-12; m=.5*(p+q)
        def kl(a,b):
            mask=a>0
            return float(np.sum(a[mask]*np.log2((a[mask]+1e-12)/(b[mask]+1e-12))))
        return .5*kl(p,m)+.5*kl(q,m)

    @staticmethod
    def normalize(x):
        med=np.median(x,axis=0); mad=np.median(np.abs(x-med),axis=0)
        mad=np.where(mad<1e-9,np.std(x,axis=0)+1e-9,mad)
        return (x-med)/(1.4826*mad+1e-9)

    @staticmethod
    def kmeans(x,k):
        k=max(1,min(k,len(x)))
        centers=[x[int(np.argmax(np.linalg.norm(x,axis=1)))]]
        while len(centers)<k:
            d=np.min(np.stack([np.linalg.norm(x-c,axis=1) for c in centers],axis=1),axis=1)
            centers.append(x[int(np.argmax(d))])
        centers=np.asarray(centers)
        labels=np.zeros(len(x),dtype=int)
        for _ in range(60):
            d=np.stack([np.linalg.norm(x-c,axis=1) for c in centers],axis=1)
            new=np.argmin(d,axis=1)
            if np.array_equal(new,labels): break
            labels=new
            centers=np.asarray([x[labels==i].mean(axis=0) if np.any(labels==i) else centers[i] for i in range(k)])
        return labels,centers

    def build(self, rows_newest_first, quantum: QuantumEngine, cqse: CQSE):
        rows=list(reversed(rows_newest_first))
        points=[]; prev=None
        for i,row in enumerate(rows):
            q16=quantum.q16(row,rows)
            surface=cqse.encode(
                f"{row['timestamp']} ping={row['ping']} jitter={row['jitter']} download={row['download_speed']} upload={row['upload_speed']}",
                q16,
            )
            labs=np.asarray(surface["oklab"],dtype=float)
            lab=labs.mean(axis=0)
            q32=surface["chromatic_quantum_32"]
            points.append({
                "index":i,"timestamp":row["timestamp"],
                "lab_l":float(lab[0]),"lab_a":float(lab[1]),"lab_b":float(lab[2]),
                "ping":float(row["ping"]),"jitter":float(row["jitter"]),
                "download":float(row["download_speed"]),"upload":float(row["upload_speed"]),
                "quantum_entropy":surface["q32_entropy"],
                "quantum_js":self.js(prev,q32) if prev is not None else 0.0,
            })
            prev=q32
        coords=np.asarray([[p["lab_l"],p["lab_a"],p["lab_b"],p["quantum_entropy"],p["quantum_js"]] for p in points])
        n=self.normalize(coords)
        velocity=np.zeros(len(points)); acceleration=np.zeros(len(points)); curvature=np.zeros(len(points))
        if len(points)>1: velocity[1:]=np.linalg.norm(np.diff(n[:,:3],axis=0),axis=1)
        if len(points)>2:
            acceleration[2:]=np.abs(np.diff(velocity[1:]))
            for i in range(1,len(points)-1):
                a=n[i,:3]-n[i-1,:3]; b=n[i+1,:3]-n[i,:3]
                na=np.linalg.norm(a); nb=np.linalg.norm(b)
                if na>1e-9 and nb>1e-9:
                    curvature[i]=math.acos(float(np.clip(np.dot(a,b)/(na*nb),-1,1)))
        change=np.zeros(len(points))
        if len(points)>1:
            delta=np.concatenate([[0],np.linalg.norm(np.diff(n,axis=0),axis=1)])
            med=np.median(delta); mad=np.median(np.abs(delta-med))
            change=np.maximum(0,(delta-med)/(1.4826*mad+1e-9))
        k=min(5,max(2,len(points)//40)) if len(points)>=8 else min(2,len(points))
        labels,centers=self.kmeans(np.column_stack([n,velocity,change]),k)
        boundaries=[]
        for i,p in enumerate(points):
            p.update({
                "velocity":float(velocity[i]),"acceleration":float(acceleration[i]),
                "curvature":float(curvature[i]),"change_score":float(change[i]),
                "regime_id":int(labels[i]),
            })
            if i and (labels[i]!=labels[i-1] or change[i]>=3):
                boundaries.append({
                    "index":i,"timestamp":p["timestamp"],
                    "from":int(labels[i-1]),"to":int(labels[i]),
                    "change_score":float(change[i]),
                    "reason":"regime-transition" if labels[i]!=labels[i-1] else "multivariate-change",
                })
        regimes=[]
        for rid in sorted(set(labels.tolist())):
            subset=[points[i] for i in np.where(labels==rid)[0]]
            regimes.append({
                "regime_id":int(rid),"samples":len(subset),
                "mean_ping":float(np.mean([p["ping"] for p in subset])),
                "mean_jitter":float(np.mean([p["jitter"] for p in subset])),
                "mean_download":float(np.mean([p["download"] for p in subset])),
                "mean_quantum_entropy":float(np.mean([p["quantum_entropy"] for p in subset])),
            })
        return {
            "generated_at":utc_now(),"point_count":len(points),
            "points":points,"regimes":regimes,"boundaries":boundaries,
            "experimental_notice":"TCQH is an experimental feature-space worldline, not literal physical quantum dynamics.",
        }


class LocalContriever:
    """Lazy optional local Meta/Facebook Contriever embedding provider."""

    def __init__(self):
        self.enabled = os.getenv("QNH_ENABLE_CONTRIEVER", "0") == "1"
        self._tokenizer = None
        self._model = None
        self._torch = None
        self._device = None
        self._lock = threading.Lock()

    def dependencies_available(self) -> bool:
        if not self.enabled:
            return False
        try:
            import torch  # noqa
            import transformers  # noqa
            return True
        except Exception:
            return False

    def _load(self):
        if not self.dependencies_available():
            raise RuntimeError("Contriever disabled or torch/transformers unavailable")
        with self._lock:
            if self._model is not None:
                return
            import torch
            from transformers import AutoModel, AutoTokenizer
            self._torch = torch
            self._tokenizer = AutoTokenizer.from_pretrained(
                FB_MODEL,
                revision=FB_REVISION,
                trust_remote_code=False,
                local_files_only=os.getenv("QNH_HF_OFFLINE", "0") == "1",
            )
            self._model = AutoModel.from_pretrained(
                FB_MODEL,
                revision=FB_REVISION,
                trust_remote_code=False,
                use_safetensors=True,
                local_files_only=os.getenv("QNH_HF_OFFLINE", "0") == "1",
            )
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            self._model.to(self._device)
            self._model.eval()

    def _embed_sync(self, text: str) -> list[float]:
        self._load()
        encoded = self._tokenizer(
            [text],
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )
        encoded = {k: v.to(self._device) for k, v in encoded.items()}
        with self._torch.no_grad():
            output = self._model(**encoded).last_hidden_state
            mask = encoded["attention_mask"].unsqueeze(-1).bool()
            output = output.masked_fill(~mask, 0.0)
            pooled = output.sum(dim=1) / mask.sum(dim=1).clamp(min=1)
        vector = pooled[0].detach().cpu().float().numpy()
        if vector.size != FB_DIM:
            raise RuntimeError(f"Unexpected Contriever dimension: {vector.size}")
        return l2(vector.tolist())

    async def embed(self, text: str) -> Optional[list[float]]:
        if not self.dependencies_available():
            return None
        return await asyncio.to_thread(self._embed_sync, text)


class GemmaIntegrityManager:
    """
    Integrity manager for the user-selected Gemma 4 E2B LiteRT-LM artifact.

    Download is opt-in through QNH_ALLOW_MODEL_DOWNLOADS=1. The model is never
    executed by this class, and .litertlm is never routed through llama.cpp.
    """

    def __init__(self):
        self.path = GEMMA_MODEL_PATH

    async def verify(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "present": False,
                "verified": False,
                "path": str(self.path),
                "expected_sha256": GEMMA_SHA256,
                "litert_lm_cli": bool(shutil.which("litert-lm")),
            }

        def digest():
            h = hashlib.sha256()
            size = 0
            with self.path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
                    size += len(chunk)
                    h.update(chunk)
            return size, h.hexdigest()

        size, actual = await asyncio.to_thread(digest)
        return {
            "present": True,
            "verified": actual.lower() == GEMMA_SHA256.lower(),
            "path": str(self.path),
            "bytes": size,
            "actual_sha256": actual,
            "expected_sha256": GEMMA_SHA256,
            "litert_lm_cli": bool(shutil.which("litert-lm")),
        }

    async def download(self) -> dict[str, Any]:
        if os.getenv("QNH_ALLOW_MODEL_DOWNLOADS", "0") != "1":
            raise RuntimeError("Model downloads are disabled by QNH_ALLOW_MODEL_DOWNLOADS")
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        part = self.path.with_suffix(self.path.suffix + ".part")
        headers = {"Accept-Encoding": "identity"}
        offset = part.stat().st_size if part.exists() else 0
        if offset:
            headers["Range"] = f"bytes={offset}-"

        expected_total = None
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30, read=None, write=60, pool=30),
            follow_redirects=True,
            verify=True,
        ) as client:
            async with client.stream("GET", GEMMA_MODEL_URL, headers=headers) as response:
                if response.url.scheme != "https":
                    raise RuntimeError("Refusing non-HTTPS model redirect")
                if offset and response.status_code != 206:
                    part.unlink(missing_ok=True)
                    raise RuntimeError("Server did not honor Range resume; partial file deleted")
                if offset:
                    cr = response.headers.get("Content-Range", "")
                    match = re.fullmatch(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", cr.strip())
                    if not match or int(match.group(1)) != offset:
                        part.unlink(missing_ok=True)
                        raise RuntimeError("Content-Range mismatch; partial file deleted")
                    if match.group(3) != "*":
                        expected_total = int(match.group(3))
                else:
                    cl = response.headers.get("Content-Length")
                    if cl and cl.isdigit():
                        expected_total = int(cl)
                response.raise_for_status()
                with part.open("ab" if offset else "wb") as fh:
                    async for chunk in response.aiter_bytes(8 * 1024 * 1024):
                        if chunk:
                            fh.write(chunk)
                    fh.flush()
                    os.fsync(fh.fileno())

        if expected_total and part.stat().st_size != expected_total:
            raise RuntimeError("Downloaded model size differs from server-declared total")

        def digest_part():
            h = hashlib.sha256()
            with part.open("rb") as fh:
                for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
                    h.update(chunk)
            return h.hexdigest()

        actual = await asyncio.to_thread(digest_part)
        if actual.lower() != GEMMA_SHA256.lower():
            part.unlink(missing_ok=True)
            raise RuntimeError(
                f"Gemma SHA-256 mismatch; bytes deleted. expected={GEMMA_SHA256} actual={actual}"
            )
        os.replace(part, self.path)
        return await self.verify()


class ResponsesReasoner:
    """Optional structured defensive analysis using a user-configured Responses model."""

    def __init__(self, key_getter):
        self.key_getter = key_getter

    @property
    def model(self) -> str:
        return os.getenv("QNH_REASONING_MODEL", "").strip()

    def configured(self) -> bool:
        return bool(self.key_getter() and self.model)

    async def analyze(self, mode: str, analytics: dict[str, Any], cqse: dict[str, Any] | None,
                      hyperfield: dict[str, Any] | None, memories: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.configured():
            raise RuntimeError("Set OPENAI_API_KEY and QNH_REASONING_MODEL to enable analysis")
        protocol = """Return concise defensive conclusions only, never private chain-of-thought.
Use:
[status]...[/status]
[observation]...[/observation]
[evidence]...[/evidence]
[risk]...[/risk]
[action]{"title":"...","priority":"high|medium|low","risk":"high|medium|low","description":"...","requires_approval":true}[/action]
[replytemplate]...[/replytemplate]
[confidence]0.0[/confidence]
[final]...[/final]
Any action is advisory and requires human approval. Do not emit destructive or credential-access actions."""
        prompt = (
            f"MODE: {mode}\n\n{protocol}\n\n"
            f"TELEMETRY:\n{json.dumps(analytics, default=str)[:18000]}\n\n"
            f"CQSE:\n{json.dumps(cqse or {}, default=str)[:10000]}\n\n"
            f"TEMPORAL HYPERFIELD:\n{json.dumps(hyperfield or {}, default=str)[:16000]}\n\n"
            f"MEMORIES:\n{json.dumps(memories[:12], default=str)[:14000]}"
        )
        async with httpx.AsyncClient(timeout=120, verify=True) as client:
            r = await client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {self.key_getter()}", "Content-Type": "application/json"},
                json={
                    "model": self.model,
                    "input": prompt,
                    "max_output_tokens": 3500,
                    "reasoning": {"effort": os.getenv("QNH_REASONING_EFFORT", "high")},
                },
            )
        r.raise_for_status()
        data = r.json()
        text = data.get("output_text") or ""
        if not text:
            parts = []
            for item in data.get("output", []) or []:
                for content in item.get("content", []) or []:
                    if isinstance(content, dict) and isinstance(content.get("text"), str):
                        parts.append(content["text"])
            text = "\n".join(parts)
        return {
            "model": data.get("model") or self.model,
            "response_id": data.get("id"),
            "usage": data.get("usage") or {},
            "text": text.strip(),
        }


class OpenAIEmbedding:
    def __init__(self, key_getter):
        self.key_getter=key_getter

    async def embed(self,text):
        key=self.key_getter()
        if not key: return None
        async with httpx.AsyncClient(timeout=60,verify=True) as client:
            r=await client.post(OPENAI_EMBED_URL,headers={"Authorization":f"Bearer {key}"},
                json={"model":OPENAI_EMBED_MODEL,"input":text,"dimensions":OPENAI_EMBED_DIM})
        r.raise_for_status()
        return l2(r.json()["data"][0]["embedding"])


class WeaviateMemory:
    def __init__(self,url_getter,key_getter,embedder:OpenAIEmbedding,cqse:CQSE,contriever:LocalContriever):
        self.url_getter=url_getter; self.key_getter=key_getter
        self.embedder=embedder; self.cqse=cqse; self.contriever=contriever

    def client(self):
        if not WEAVIATE_OK: raise RuntimeError("weaviate-client not installed")
        url=self.url_getter()
        key=self.key_getter()
        if url and url.startswith(("http://","https://")):
            from urllib.parse import urlparse
            p=urlparse(url)
            return weaviate.use_async_with_custom(
                http_host=p.hostname,
                http_port=p.port or (443 if p.scheme=="https" else 80),
                http_secure=p.scheme=="https",
                grpc_host=p.hostname,
                grpc_port=int(os.getenv("WEAVIATE_GRPC_PORT","50051")),
                grpc_secure=p.scheme=="https",
                auth_credentials=Auth.api_key(key) if key else None,
            )
        return weaviate.use_async_with_local(
            auth_credentials=Auth.api_key(key) if key else None
        )

    @staticmethod
    def vectors(schema):
        v=[Configure.Vectors.self_provided(name=VEC_OPENAI),
           Configure.Vectors.self_provided(name=VEC_FB),
           Configure.Vectors.self_provided(name=VEC_CQ96),
           Configure.Vectors.self_provided(name=VEC_CQQ32)]
        if schema.get("quantum"):
            v.append(Configure.Vectors.self_provided(name=VEC_Q16))
        return v

    async def setup(self):
        result={}
        async with self.client() as client:
            for name,schema in COLLECTIONS.items():
                if await client.collections.exists(name):
                    result[name]="exists"; continue
                props=[Property(name=n,data_type=getattr(DataType,t)) for n,t in schema["properties"]]
                await client.collections.create(name=name,vector_config=self.vectors(schema),properties=props)
                result[name]="created"
        return result

    async def status(self):
        async with self.client() as client:
            ready=bool(await client.is_ready())
            cols={}
            for name in COLLECTIONS:
                cols[name]=bool(await client.collections.exists(name))
            return {"ready":ready,"collections":cols}

    async def insert(self,name,props,q16=None,use_frontier_embeddings=True):
        text="\n".join(f"{k}: {v}" for k,v in props.items() if k in {"summary","title","evidence","description"})
        if not text: text=safe_json(props)
        openai_vec = await self.embedder.embed(text) if use_frontier_embeddings else None
        fb_vec = await self.contriever.embed(text) if use_frontier_embeddings else None
        c=self.cqse.encode(text,q16)
        vectors={VEC_CQ96:c["chromatic_surface_96"],VEC_CQQ32:c["chromatic_quantum_32"]}
        if openai_vec: vectors[VEC_OPENAI]=openai_vec
        if fb_vec: vectors[VEC_FB]=fb_vec
        if q16 and COLLECTIONS[name].get("quantum"): vectors[VEC_Q16]=q16
        object_id=uuid.uuid5(uuid.NAMESPACE_URL,f"qnh://{name}/{stable_hash(text)[:32]}")
        async with self.client() as client:
            coll=client.collections.use(name)
            if await coll.data.exists(object_id):
                await coll.data.update(uuid=object_id,properties=props,vector=vectors)
            else:
                await coll.data.insert(uuid=object_id,properties=props,vector=vectors)
        return str(object_id)

    async def search(self,query,limit=12):
        openai_vec=await self.embedder.embed(query)
        fb_vec=await self.contriever.embed(query)
        c=self.cqse.encode(query)
        all_hits=[]
        async with self.client() as client:
            for name in COLLECTIONS:
                if not await client.collections.exists(name): continue
                coll=client.collections.use(name)
                ranked=[]
                try:
                    r=await coll.query.bm25(query=query,limit=limit,return_metadata=MetadataQuery(score=True))
                    ranked.append(("bm25",list(r.objects),.7))
                except Exception: pass
                for vec_name,vec,weight in [
                    (VEC_OPENAI,openai_vec,1.0),
                    (VEC_FB,fb_vec,.90),
                    (VEC_CQ96,c["chromatic_surface_96"],.78),
                    (VEC_CQQ32,c["chromatic_quantum_32"],.62),
                ]:
                    if vec is None: continue
                    try:
                        r=await coll.query.near_vector(
                            near_vector=vec,target_vector=vec_name,limit=limit,
                            return_metadata=MetadataQuery(distance=True))
                        ranked.append((vec_name,list(r.objects),weight))
                    except Exception: pass
                fused={}
                for source,objects,weight in ranked:
                    for rank,obj in enumerate(objects,1):
                        key=str(obj.uuid)
                        item=fused.setdefault(key,{"uuid":key,"collection":name,"score":0.0,
                                                    "sources":[],"properties":dict(obj.properties or {})})
                        item["score"]+=weight/(60+rank)
                        item["sources"].append({"source":source,"rank":rank})
                all_hits.extend(fused.values())
        all_hits.sort(key=lambda x:x["score"],reverse=True)
        return all_hits[:limit]


DATASET_MANIFESTS = [
    ("lanl_unified","LANL Unified Host/Network","network+host"),
    ("lanl_multisource","LANL Multi-Source Events","enterprise-events"),
    ("lanl_auth","LANL User-Computer Authentication","authentication"),
    ("cic_family","Canadian Institute for Cybersecurity family","intrusion"),
    ("kdd99","KDD Cup 1999","intrusion"),
    ("agung_malware","Network Malware Detection Connection Analysis","network-malware"),
]


def simulate_dataset(dataset_id: str, count: int = 200):
    info=next((x for x in DATASET_MANIFESTS if x[0]==dataset_id),None)
    if not info: raise KeyError(dataset_id)
    rng=random.Random(int(stable_hash(dataset_id)[:8],16))
    records=[]
    for i in range(max(1,min(count,2000))):
        label=rng.choices(["benign","scan","bruteforce","dos","botnet-like"],[78,7,6,5,4])[0]
        scale={"benign":1,"scan":1.6,"bruteforce":1.9,"dos":4.1,"botnet-like":2.5}[label]
        f={
            "duration":round(rng.lognormvariate(2.5,.8)*scale,4),
            "src_bytes":round(rng.lognormvariate(6.0,1.1)*scale,3),
            "dst_bytes":round(rng.lognormvariate(5.7,1.0)/max(scale/1.3,1),3),
            "packets":int(rng.lognormvariate(2.2,.7)*scale),
            "fanout":round(min(1,rng.random()*.45*scale),5),
            "burst":round(min(1,rng.random()*.32*scale),5),
        }
        rid=stable_hash(dataset_id,str(i),safe_json(f))[:32]
        records.append({
            "record_id":rid,"dataset_id":dataset_id,"dataset_name":info[1],
            "simulated":True,"label":label,"category":info[2],
            "summary":f"SIMULATED {info[1]} record {i}; label={label}; features={safe_json(f)}",
            "features_json":safe_json(f),"ingested_at":utc_now(),
            "tags":["simulated",dataset_id,info[2]],
        })
    return records


@dataclass
class Runtime:
    loop: AsyncLoop
    store: AsyncStore
    quantum: QuantumEngine
    cqse: CQSE
    hyperfield: Hyperfield
    memory: WeaviateMemory
    contriever: LocalContriever
    gemma: GemmaIntegrityManager
    reasoner: ResponsesReasoner
