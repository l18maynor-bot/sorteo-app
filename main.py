from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import httpx, os, hashlib, secrets, random, base64
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="SorteoApp API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Supabase ──────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET_KEY")
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

# ── PayPal ────────────────────────────────────
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID")
PAYPAL_SECRET    = os.getenv("PAYPAL_SECRET")
PAYPAL_MODE      = os.getenv("PAYPAL_MODE", "sandbox")
PAYPAL_BASE      = "https://api-m.sandbox.paypal.com" if PAYPAL_MODE == "sandbox" else "https://api-m.paypal.com"
PLAN_PRICE       = "9.99"  # USD — cambia cuando pases a Live
PLAN_CURRENCY    = "USD"

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

async def get_paypal_token() -> str:
    credentials = base64.b64encode(f"{PAYPAL_CLIENT_ID}:{PAYPAL_SECRET}".encode()).decode()
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{PAYPAL_BASE}/v1/oauth2/token",
            headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/x-www-form-urlencoded"},
            data="grant_type=client_credentials"
        )
        return r.json()["access_token"]

# ── Modelos ───────────────────────────────────
class UserRegister(BaseModel):
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class PaymentCapture(BaseModel):
    order_id: str
    email: str

# ── RUTAS ─────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse("frontend/index.html")

# ── REGISTRO ──────────────────────────────────
@app.post("/auth/register")
async def register(user: UserRegister):
    async with httpx.AsyncClient() as client:
        check = await client.get(
            f"{SUPABASE_URL}/rest/v1/usuarios?email=eq.{user.email}",
            headers=HEADERS
        )
        if check.json():
            raise HTTPException(400, "Este email ya está registrado")
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
        token = secrets.token_hex(32)
        return {
            "ok": True,
            "token": token,
            "email": db_user["email"],
            "plan": db_user["plan"],
            "sorteos_mes": db_user["sorteos_mes"]
        }

# ── PAYPAL: Crear orden ───────────────────────
@app.post("/payment/create")
async def create_payment(data: dict):
    email = data.get("email")
    if not email:
        raise HTTPException(400, "Email requerido")
    try:
        token = await get_paypal_token()
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{PAYPAL_BASE}/v2/checkout/orders",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "intent": "CAPTURE",
                    "purchase_units": [{
                        "amount": {"currency_code": PLAN_CURRENCY, "value": PLAN_PRICE},
                        "description": f"SorteoApp Pro — {email}"
                    }],
                    "application_context": {
                        "brand_name": "SorteoApp",
                        "user_action": "PAY_NOW"
                    }
                }
            )
            order = r.json()
            return {"order_id": order["id"], "status": order["status"]}
    except Exception as e:
        raise HTTPException(500, f"Error al crear pago: {str(e)}")

# ── PAYPAL: Capturar pago ─────────────────────
@app.post("/payment/capture")
async def capture_payment(data: PaymentCapture):
    try:
        token = await get_paypal_token()
        async with httpx.AsyncClient() as client:
            # Capturar el pago en PayPal
            r = await client.post(
                f"{PAYPAL_BASE}/v2/checkout/orders/{data.order_id}/capture",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            result = r.json()
            if result.get("status") != "COMPLETED":
                raise HTTPException(400, "Pago no completado")

            # Actualizar plan en Supabase
            update = await client.patch(
                f"{SUPABASE_URL}/rest/v1/usuarios?email=eq.{data.email}",
                headers={**HEADERS, "Prefer": "return=representation"},
                json={"plan": "pro", "sorteos_mes": 0}
            )
            return {
                "ok": True,
                "mensaje": "¡Pago exitoso! Plan actualizado a Pro",
                "plan": "pro"
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error al capturar pago: {str(e)}")

# ── SORTEO ────────────────────────────────────
@app.post("/sorteo/run")
async def run_sorteo(data: dict):
    comments = data.get("comments", [])
    config   = data.get("config", {})
    if config.get("no_dupes", True):
        seen, unique = set(), []
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
