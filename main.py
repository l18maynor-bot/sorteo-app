from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx, os, hashlib, secrets
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="SorteoApp API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET_KEY")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

class UserRegister(BaseModel):
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

@app.get("/")
async def root():
    return FileResponse("frontend/index.html")

# ── REGISTRO ──────────────────────────────────
@app.post("/auth/register")
async def register(user: UserRegister):
    async with httpx.AsyncClient() as client:
        # Verificar si ya existe
        check = await client.get(
            f"{SUPABASE_URL}/rest/v1/usuarios?email=eq.{user.email}",
            headers=HEADERS
        )
        if check.json():
            raise HTTPException(400, "Este email ya está registrado")
        # Crear usuario
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/usuarios",
            headers={**HEADERS, "Prefer": "return=representation"},
            json={
                "email": user.email,
                "password_hash": hash_password(user.password),
                "plan": "gratis",
                "sorteos_mes": 0
            }
        )
        if r.status_code not in [200, 201]:
            raise HTTPException(500, "Error al crear usuario")
        return {"ok": True, "mensaje": "Cuenta creada exitosamente"}

# ── LOGIN ─────────────────────────────────────
@app.post("/auth/login")
async def login(user: UserLogin):
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/usuarios?email=eq.{user.email}",
            headers=HEADERS
        )
        data = r.json()
        if not data:
            raise HTTPException(401, "Email o contraseña incorrectos")
        db_user = data[0]
        if db_user["password_hash"] != hash_password(user.password):
            raise HTTPException(401, "Email o contraseña incorrectos")
        # Token simple
        token = secrets.token_hex(32)
        return {
            "ok": True,
            "token": token,
            "email": db_user["email"],
            "plan": db_user["plan"],
            "sorteos_mes": db_user["sorteos_mes"]
        }

# ── SORTEO ────────────────────────────────────
@app.post("/sorteo/run")
async def run_sorteo(data: dict):
    import random
    comments = data.get("comments", [])
    config = data.get("config", {})
    if config.get("no_dupes", True):
        seen = set()
        unique = []
        for c in comments:
            uid = c.get("from", {}).get("id") or c.get("username") or c.get("name")
            if uid not in seen:
                seen.add(uid)
                unique.append(c)
        comments = unique
    n = config.get("winners", 1)
    if len(comments) < n:
        raise HTTPException(400, "No hay suficientes participantes")
    winners = random.sample(comments, n)
    return {"winners": winners, "total": len(comments)}
