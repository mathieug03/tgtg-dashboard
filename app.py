from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn
from contextlib import asynccontextmanager
import asyncio
from pathlib import Path
from dotenv import load_dotenv
from tgtg_client import TgtgApiClient, token_expiry
from bot_manager import bot_manager
from credential_store import credential_session, load_credentials, save_credentials

PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / '.env')


@asynccontextmanager
async def lifespan(app):
    bot_manager.start()
    try:
        yield
    finally:
        await asyncio.to_thread(bot_manager.stop)


app = FastAPI(title="TGTG API Proxy", lifespan=lifespan)

@app.middleware("http")
async def prevent_stale_credentials(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


class AuthRequest(BaseModel):
    email: str

class PinSubmitRequest(BaseModel):
    email: str
    pin: str
    polling_id: str

class BaseTokensRequest(BaseModel):
    access_token: str
    refresh_token: str
    cookie: str | None = None

class ItemsRequest(BaseTokensRequest):
    latitude: float = 48.8566
    longitude: float = 2.3522

class OrderCreateRequest(BaseTokensRequest):
    item_id: str
    item_count: int = 1

class OrderRequest(BaseTokensRequest):
    order_id: str

class BotAddRequest(BaseTokensRequest):
    item_id: str
    target_time: float | None = None

class BotRemoveRequest(BaseModel):
    item_id: str


@app.post("/api/auth/start")
def auth_start(req: AuthRequest):
    client = TgtgApiClient()
    try:
        data = client.request_auth_code(req.email)
        return {"status": "success", "polling_id": data.get("polling_id"), "message": "Check email for PIN."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/auth/verify")
def auth_verify(req: PinSubmitRequest):
    client = TgtgApiClient()
    try:
        data = client.submit_pin(req.email, req.pin, req.polling_id)
        save_credentials(data)
        return {"status": "success", "credentials": data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/auth/status")
def auth_status():
    saved = load_credentials()
    return {"has_credentials": bool(saved.get("access_token")),
            "expires_at": token_expiry(saved.get("access_token")),
            "can_refresh": bool(saved.get("refresh_token"))}


@app.post("/api/auth/refresh")
def auth_refresh(req: BaseTokensRequest):
    try:
        with credential_session(req.model_dump()) as client:
            client.refresh_credentials()
            expires_at = token_expiry(client.access_token)
        return {"status": "success", "expires_at": expires_at}
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error))


@app.post("/api/items")
def get_items(req: ItemsRequest):
    try:
        with credential_session(req.model_dump()) as client:
            items = client.get_items(latitude=req.latitude, longitude=req.longitude)
        return {"status": "success", "items": items}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/favorites")
def get_favorites(req: ItemsRequest):
    try:
        with credential_session(req.model_dump()) as client:
            items = client.get_favorites(latitude=req.latitude, longitude=req.longitude)
        return {"status": "success", "items": items}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/order/create")
def create_order(req: OrderCreateRequest):
    try:
        with credential_session(req.model_dump()) as client:
            data = client.create_order(item_id=req.item_id, item_count=req.item_count)
        return {"status": "success", "order": data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
        
@app.post("/api/order/status")
def get_order_status(req: OrderRequest):
    try:
        with credential_session(req.model_dump()) as client:
            data = client.get_order_status(order_id=req.order_id)
        return {"status": "success", "order": data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/order/active")
def get_active_orders(req: BaseTokensRequest):
    try:
        with credential_session(req.model_dump()) as client:
            data = client.get_active_orders()
        return {"status": "success", "orders": data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/order/abort")
def abort_order(req: OrderRequest):
    try:
        with credential_session(req.model_dump()) as client:
            data = client.abort_order(order_id=req.order_id)
        bot_manager.mark_order_aborted(req.order_id)
        return {"status": "success", "order": data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/bot/start")
def bot_add(req: BotAddRequest):
    success = bot_manager.add_sniper(req.item_id, req.target_time, req.model_dump())
    if not success:
        raise HTTPException(status_code=400, detail="Bot already active for this item")
    return {"status": "success"}

@app.post("/api/bot/stop")
def bot_remove(req: BotRemoveRequest):
    bot_manager.remove_sniper(req.item_id)
    return {"status": "success"}

@app.get("/api/bot/status")
def bot_status():
    return {"status": "success", "snipers": bot_manager.get_status()}

@app.get("/api/credentials")
def get_credentials():
    return load_credentials()

app.mount("/", StaticFiles(directory=PROJECT_DIR / "static", html=True), name="static")

if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
