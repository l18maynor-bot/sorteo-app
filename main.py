from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse
from pydantic import BaseModel
import httpx, os, hashlib, secrets, random, base64
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

PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID")
PAYPAL_SECRET    = os.getenv("PAYPAL_SECRET")
PAYPAL_MODE      = os.getenv("PAYPAL_MODE", "sandbox")
PAYPAL_BASE      = "https://api-m.sandbox.paypal.com" if PAYPAL_MODE == "sandbox" else "https://api-m.paypal.com"
PLAN_PRICE       = "9.99"
PLAN_CURRENCY    = "USD"
LIMITE_GRATIS    = 3
ADMIN_SECRET     = os.getenv("ADMIN_SECRET", "sorteo_admin_2025")

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

class UserRegister(BaseModel):
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class PaymentCapture(BaseModel):
    order_id: str
    email: str

@app.get("/")
async def root():
    return FileResponse("frontend/landing.html")

@app.get("/app")
async def app_page():
    return FileResponse("frontend/index.html")

@app.get("/admin")
async def admin_panel():
    return FileResponse("frontend/admin.html")

# ── ADMIN: datos de usuarios ──────────────────────────────────────────────────
@app.get("/admin/data")
async def admin_data(secret: str = Query(...)):
    if secret != ADMIN_SECRET:
        raise HTTPException(403, "No autorizado")
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/usuarios?select=email,plan,sorteos_mes,created_at&order=created_at.desc",
            headers=HEADERS
        )
        usuarios = r.json()
        total = len(usuarios)
        pro = sum(1 for u in usuarios if u.get("plan") == "pro")
        gratis = total - pro
        sorteos_total = sum(u.get("sorteos_mes", 0) for u in usuarios)
        return {
            "stats": {
                "total_usuarios": total,
                "usuarios_pro": pro,
                "usuarios_gratis": gratis,
                "sorteos_este_mes": sorteos_total,
                "mrr": round(pro * 9.99, 2)
            },
            "usuarios": usuarios
        }

# ── ADMIN: cambiar plan de usuario ────────────────────────────────────────────
@app.post("/admin/set-plan")
async def admin_set_plan(data: dict):
    if data.get("secret") != ADMIN_SECRET:
        raise HTTPException(403, "No autorizado")
    email = data.get("email")
    plan  = data.get("plan")
    if not email or plan not in ["gratis", "pro"]:
        raise HTTPException(400, "Datos inválidos")
    async with httpx.AsyncClient() as client:
        await client.patch(
            f"{SUPABASE_URL}/rest/v1/usuarios?email=eq.{email}",
            headers={**HEADERS, "Prefer": "return=representation"},
            json={"plan": plan, "sorteos_mes": 0}
        )
    return {"ok": True, "mensaje": f"Plan de {email} actualizado a {plan}"}

# ── ADMIN: resetear contador de sorteos ───────────────────────────────────────
@app.post("/admin/reset-counter")
async def admin_reset_counter(data: dict):
    if data.get("secret") != ADMIN_SECRET:
        raise HTTPException(403, "No autorizado")
    email = data.get("email")
    if not email:
        raise HTTPException(400, "Email requerido")
    async with httpx.AsyncClient() as client:
        await client.patch(
            f"{SUPABASE_URL}/rest/v1/usuarios?email=eq.{email}",
            headers={**HEADERS, "Prefer": "return=representation"},
            json={"sorteos_mes": 0}
        )
    return {"ok": True}

@app.post("/auth/register")
async def register(user: UserRegister):
    async with httpx.AsyncClient() as client:
        check = await client.get(f"{SUPABASE_URL}/rest/v1/usuarios?email=eq.{user.email}", headers=HEADERS)
        if check.json():
            raise HTTPException(400, "Este email ya está registrado")
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/usuarios",
            headers={**HEADERS, "Prefer": "return=representation"},
            json={"email": user.email, "password_hash": hash_password(user.password), "plan": "gratis", "sorteos_mes": 0}
        )
        if r.status_code not in [200, 201]:
            raise HTTPException(500, "Error al crear usuario")
        return {"ok": True, "mensaje": "Cuenta creada exitosamente"}

@app.post("/auth/login")
async def login(user: UserLogin):
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{SUPABASE_URL}/rest/v1/usuarios?email=eq.{user.email}", headers=HEADERS)
        data = r.json()
        if not data:
            raise HTTPException(401, "Email o contraseña incorrectos")
        db_user = data[0]
        if db_user["password_hash"] != hash_password(user.password):
            raise HTTPException(401, "Email o contraseña incorrectos")
        token = secrets.token_hex(32)
        return {"ok": True, "token": token, "email": db_user["email"], "plan": db_user["plan"], "sorteos_mes": db_user["sorteos_mes"]}

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
                    "purchase_units": [{"amount": {"currency_code": PLAN_CURRENCY, "value": PLAN_PRICE}, "description": f"SorteoApp Pro — {email}"}],
                    "application_context": {"brand_name": "SorteoApp", "user_action": "PAY_NOW"}
                }
            )
            order = r.json()
            return {"order_id": order["id"], "status": order["status"]}
    except Exception as e:
        raise HTTPException(500, f"Error al crear pago: {str(e)}")

@app.post("/payment/capture")
async def capture_payment(data: PaymentCapture):
    try:
        token = await get_paypal_token()
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{PAYPAL_BASE}/v2/checkout/orders/{data.order_id}/capture",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            result = r.json()
            if result.get("status") != "COMPLETED":
                raise HTTPException(400, "Pago no completado")
            await client.patch(
                f"{SUPABASE_URL}/rest/v1/usuarios?email=eq.{data.email}",
                headers={**HEADERS, "Prefer": "return=representation"},
                json={"plan": "pro", "sorteos_mes": 0}
            )
            return {"ok": True, "mensaje": "¡Pago exitoso! Plan actualizado a Pro", "plan": "pro"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error al capturar pago: {str(e)}")

@app.post("/sorteo/run")
async def run_sorteo(data: dict):
    email    = data.get("email")
    comments = data.get("comments", [])
    config   = data.get("config", {})
    if email:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{SUPABASE_URL}/rest/v1/usuarios?email=eq.{email}", headers=HEADERS)
            users = r.json()
            if users:
                u = users[0]
                if u["plan"] == "gratis" and u["sorteos_mes"] >= LIMITE_GRATIS:
                    raise HTTPException(403, f"Límite de {LIMITE_GRATIS} sorteos mensuales alcanzado. ¡Upgrade a Pro!")
                await client.patch(
                    f"{SUPABASE_URL}/rest/v1/usuarios?email=eq.{email}",
                    headers={**HEADERS, "Prefer": "return=representation"},
                    json={"sorteos_mes": u["sorteos_mes"] + 1}
                )
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

META_APP_ID       = os.getenv("META_APP_ID")
META_APP_SECRET   = os.getenv("META_APP_SECRET")
META_REDIRECT_URI = os.getenv("META_REDIRECT_URI", "https://appsorteos.up.railway.app/meta/callback")
META_SCOPES       = "pages_show_list"

@app.get("/meta/login")
async def meta_login():
    url = (f"https://www.facebook.com/v19.0/dialog/oauth?client_id={META_APP_ID}&redirect_uri={META_REDIRECT_URI}&scope={META_SCOPES}&response_type=code")
    return RedirectResponse(url)

@app.get("/meta/callback")
async def meta_callback(code: str = None, error: str = None):
    if error or not code:
        return HTMLResponse("<script>window.opener&&window.opener.postMessage('meta_error=acceso_denegado','*');window.close();</script>")
    async with httpx.AsyncClient() as client:
        r = await client.get("https://graph.facebook.com/v19.0/oauth/access_token",
            params={"client_id": META_APP_ID, "client_secret": META_APP_SECRET, "redirect_uri": META_REDIRECT_URI, "code": code})
        short_token = r.json().get("access_token")
        if not short_token:
            return HTMLResponse("<script>window.opener&&window.opener.postMessage('meta_error=token_fallido','*');window.close();</script>")
        r2 = await client.get("https://graph.facebook.com/v19.0/oauth/access_token",
            params={"grant_type": "fb_exchange_token", "client_id": META_APP_ID, "client_secret": META_APP_SECRET, "fb_exchange_token": short_token})
        long_token = r2.json().get("access_token", short_token)
    return HTMLResponse(f"<script>window.opener&&window.opener.postMessage('meta_token={long_token}','*');window.close();</script>")

@app.get("/meta/posts")
async def meta_get_posts(token: str, source: str = "facebook"):
    async with httpx.AsyncClient() as client:
        if source == "instagram":
            pages_r = await client.get("https://graph.facebook.com/v19.0/me/accounts", params={"access_token": token, "fields": "id,name,instagram_business_account"})
            pages = pages_r.json().get("data", [])
            ig_accounts = [{"id": p["instagram_business_account"]["id"], "name": p["name"]} for p in pages if p.get("instagram_business_account")]
            if not ig_accounts:
                raise HTTPException(404, "No se encontró cuenta de Instagram Business conectada")
            ig = ig_accounts[0]
            posts_r = await client.get(f"https://graph.facebook.com/v19.0/{ig['id']}/media", params={"access_token": token, "fields": "id,caption,timestamp,media_type,permalink", "limit": 20})
            return {"source": "instagram", "account": ig["name"], "posts": posts_r.json().get("data", [])}
        else:
            pages_r = await client.get("https://graph.facebook.com/v19.0/me/accounts", params={"access_token": token, "fields": "id,name,access_token"})
            pages = pages_r.json().get("data", [])
            if not pages:
                raise HTTPException(404, "No se encontraron páginas de Facebook")
            page = pages[0]
            feed_r = await client.get(f"https://graph.facebook.com/v19.0/{page['id']}/posts", params={"access_token": page["access_token"], "fields": "id,message,created_time,permalink_url", "limit": 20})
            return {"source": "facebook", "page": page["name"], "posts": feed_r.json().get("data", [])}

@app.get("/meta/comments")
async def meta_get_comments(token: str, post_id: str, source: str = "facebook"):
    async with httpx.AsyncClient() as client:
        if source == "instagram":
            r = await client.get(f"https://graph.facebook.com/v19.0/{post_id}/comments", params={"access_token": token, "fields": "id,text,username,timestamp", "limit": 500})
        else:
            r = await client.get(f"https://graph.facebook.com/v19.0/{post_id}/comments", params={"access_token": token, "fields": "id,message,from,created_time", "limit": 500})
        data = r.json()
        if "error" in data:
            raise HTTPException(400, f"Error Meta API: {data['error'].get('message')}")
        normalized = []
        for c in data.get("data", []):
            if source == "instagram":
                normalized.append({"id": c.get("id"), "username": c.get("username",""), "name": c.get("username",""), "text": c.get("text",""), "timestamp": c.get("timestamp")})
            else:
                frm = c.get("from", {})
                normalized.append({"id": c.get("id"), "username": frm.get("id",""), "name": frm.get("name",""), "text": c.get("message",""), "timestamp": c.get("created_time")})
        return {"total": len(normalized), "comments": normalized}
