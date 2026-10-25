"""Penyiar status agen ke halaman Kantor Virtual (SSE).

Pakai di main.py:
    import office_hub
    office_hub.start()                       # sekali saja, sebelum bot jalan
    office_hub.set_status("DevMatrix", "working", "Menulis kode ESP32")
    office_hub.set_status("DevMatrix", "done")
"""
import asyncio, json, os, threading
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

AGENTS = ["DevMatrix", "Nexus", "Aegis", "Sentinel", "Pulse"]
VIEW_TOKEN = os.environ.get("VIEW_TOKEN", "")   # isi di Environment Render

app = FastAPI()
_state = {n: {"name": n, "state": "idle", "text": ""} for n in AGENTS}
_clients = set()
_loop = None


def set_status(name, state, text=""):
    """Aman dipanggil dari thread/async mana pun. state: working|thinking|done|idle."""
    ev = {"name": name, "state": state, "text": text[:80]}   # potong, jangan bocorkan isi chat
    _state[name] = ev
    if _loop:
        _loop.call_soon_threadsafe(_broadcast, ev)


def _broadcast(ev):
    for q in list(_clients):
        q.put_nowait(ev)


def _check(k):
    if not VIEW_TOKEN or k != VIEW_TOKEN:
        raise HTTPException(status_code=404)


@app.on_event("startup")
async def _startup():
    global _loop
    _loop = asyncio.get_running_loop()


@app.get("/")
def index(k: str = ""):
    _check(k)
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


@app.get("/events")
async def events(request: Request, k: str = ""):
    _check(k)
    q = asyncio.Queue()
    _clients.add(q)

    async def gen():
        try:
            for ev in list(_state.values()):
                yield f"data: {json.dumps(ev)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), 15)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            _clients.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def start():
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(
        target=lambda: uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning"),
        daemon=True).start()
