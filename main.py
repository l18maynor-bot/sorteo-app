from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, FileResponse
import httpx, os, random
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="SorteoApp API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

APP_ID = os.getenv("META_APP_ID")
APP_SECRET = os.getenv("META_APP_SECRET")
REDIRECT_URI = os.getenv("META_REDIRECT_URI")

@app.get("/")
async def root():
    return FileResponse("frontend/index.html")

@app.get("/auth/login")
async def login():
    url = (
        f"https://www.facebook.com/v19.0/dialog/oauth"
        f"?client_id={APP_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope=pages_show_list,pages_read_engagement,instagram_basic,instagram_manage_comments"
        f"&response_type=code"
    )
    return RedirectResponse(url)

@app.get("/auth/callback")
async def auth_callback(code: str = Query(...)):
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://graph.facebook.com/v19.0/oauth/access_token",
            params={"client_id": APP_ID, "client_secret": APP_SECRET,
                    "redirect_uri": REDIRECT_URI, "code": code}
        )
        return r.json()

@app.get("/fb/comments")
async def get_fb_comments(post_id: str, page_token: str):
    comments = []
    url = f"https://graph.facebook.com/v19.0/{post_id}/comments"
    params = {"access_token": page_token, "fields": "id,from,message,created_time", "limit": 100}
    async with httpx.AsyncClient() as client:
        while url:
            r = await client.get(url, params=params)
            data = r.json()
            comments.extend(data.get("data", []))
            url = data.get("paging", {}).get("next")
            params = {}
    return {"comments": comments, "total": len(comments)}

@app.get("/ig/comments")
async def get_ig_comments(media_id: str, access_token: str):
    comments = []
    url = f"https://graph.facebook.com/v19.0/{media_id}/comments"
    params = {"access_token": access_token, "fields": "id,username,text,timestamp", "limit": 100}
    async with httpx.AsyncClient() as client:
        while url:
            r = await client.get(url, params=params)
            data = r.json()
            comments.extend(data.get("data", []))
            url = data.get("paging", {}).get("next")
            params = {}
    return {"comments": comments, "total": len(comments)}

@app.post("/sorteo/run")
async def run_sorteo(data: dict):
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
