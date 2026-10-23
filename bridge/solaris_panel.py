# /// script
# requires-python = ">=3.11"
# dependencies = ["bleak>=0.22", "aiohttp>=3.9"]
# ///
"""Painel local do Haylou Solar Ultra (LS23): saúde, aparelho e controles no navegador.

Cada ação abre uma conexão BLE curta e desconecta no fim (o relógio só aceita uma por vez),
então o celular pode reconectar enquanto o painel está parado.

Uso:
    uv run solaris_panel.py               # abre http://localhost:8765
    uv run solaris_panel.py --port 9000 --no-browser
"""

import argparse
import time
import traceback
import webbrowser
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from aiohttp import web
from bleak import BleakClient

from solaris_read import (
    DATA1_READ, DATA_SERVICE, EOF, HIST_HR, HIST_SLEEP, HIST_STEPS, SOF, WATCH_ADDRESS,
    Link, device_info, find, history_range,
)
from solaris_notify import CMD_PUSH_MESSAGE, SET, TYPES, build_message, send

PAGE = Path(__file__).with_name("painel.html")
CMD_BATTERY, CMD_DEVICE_INFO, CMD_HR_INTERVAL, CMD_ACK = 0x08, 0x03, 0x5C, 0x01
HR_INTERVALS = (0, 3, 10)  # minutos já testados no relógio (0 = desligado)


def acked(ack: bytes | None, cmd: int) -> bool:
    """Confirmação de SET: 6F 01 81 02 00 <cmd> <status> 8F, status 0 = OK."""
    return bool(ack and len(ack) >= 7 and ack[1] == CMD_ACK and ack[5] == cmd and ack[6] == 0)


class Watch:
    def __init__(self, address: str | None, timeout: float) -> None:
        self.address = address
        self.timeout = timeout
        self.busy = False
        self.last: dict | None = None

    @asynccontextmanager
    async def connect(self):
        """Uma conexão por vez; quem chega enquanto outra está aberta recebe 409."""
        if self.busy:
            raise web.HTTPConflict(text="O painel já está falando com o relógio. Espere terminar.")
        self.busy = True
        try:
            dev = await find(self.address, self.timeout)
            if not dev:
                raise web.HTTPServiceUnavailable(
                    text="Relógio não encontrado. Desligue o Bluetooth do celular e tente de novo."
                )
            async with BleakClient(dev) as client:
                if not client.services.get_service(DATA_SERVICE):
                    raise web.HTTPServiceUnavailable(text="Serviço de dados 0x6006 não encontrado.")
                link = Link(client, raw=False)
                await client.start_notify(DATA1_READ, link.on_notify)
                yield dev, client, link
        finally:
            self.busy = False

    async def read_all(self, days: int) -> dict:
        start, end = history_range(days)
        async with self.connect() as (dev, client, link):
            out: dict = {"nome": dev.name, "endereco": dev.address, "lido_em": end,
                         "inicio": start, "fim": end, "dias": days}

            bat = await link.request(CMD_BATTERY, b"\x01")
            if bat and len(bat[0]) >= 2:
                b = bat[0]
                out["bateria"] = {"nivel": b[1], "carregando": b[0] == 1,
                                  "economia": len(b) >= 3 and b[2] == 1}

            info = await link.request(CMD_DEVICE_INFO, b"\x08")
            out["aparelho"] = device_info(info[0]) if info and len(info[0]) > 1 else {}

            interval = await link.request(CMD_HR_INTERVAL, b"\x00")
            out["intervalo_bpm"] = interval[0][0] if interval and interval[0] else None

            steps = await link.history(HIST_STEPS, start, end)
            out["passos"] = [{"t": p["t"], "passos": p.get(3, 0), "kcal": p.get(4, 0) / 1000,
                              "metros": p.get(5, 0)} for p in steps]
            out["batimentos"] = [[p["t"], p[7]] for p in await link.history(HIST_HR, start, end) if p.get(7)]
            out["sono"] = [[p["t"], p[13]] for p in await link.history(HIST_SLEEP, start, end) if 13 in p]

        self.last = out
        return out

    async def notify(self, title: str, content: str, kind: str) -> None:
        async with self.connect() as (_, client, link):
            pkt = build_message(int(time.time()) & 0x7FFFFFFF, TYPES[kind], title, content, datetime.now())
            if not acked(await send(client, link, pkt), CMD_PUSH_MESSAGE):
                raise web.HTTPBadGateway(text="O relógio não confirmou a notificação.")

    async def set_hr_interval(self, minutes: int) -> int:
        async with self.connect() as (_, client, link):
            pkt = bytes([SOF, CMD_HR_INTERVAL, SET, 1, 0, minutes, EOF])
            if not acked(await send(client, link, pkt), CMD_HR_INTERVAL):
                raise web.HTTPBadGateway(text="O relógio não confirmou o novo intervalo.")
            back = await link.request(CMD_HR_INTERVAL, b"\x00")
        value = back[0][0] if back and back[0] else minutes
        if self.last is not None:
            self.last["intervalo_bpm"] = value
        return value


def make_app(watch: Watch) -> web.Application:
    async def page(_):
        return web.FileResponse(PAGE)

    async def config(_):
        return web.json_response({"tipos": sorted(TYPES), "intervalos": HR_INTERVALS, "ultimo": watch.last})

    async def dados(req: web.Request):
        days = int(req.query.get("dias", 1))
        return web.json_response(await watch.read_all(max(1, min(days, 7))))

    async def notificar(req: web.Request):
        body = await req.json()
        title, content = str(body.get("titulo", "")).strip(), str(body.get("mensagem", "")).strip()
        kind = body.get("tipo", "sms")
        if not title or not content or kind not in TYPES:
            raise web.HTTPBadRequest(text="Preencha título, mensagem e um tipo válido.")
        await watch.notify(title, content, kind)
        return web.json_response({"ok": True})

    async def intervalo(req: web.Request):
        minutes = (await req.json()).get("minutos")
        if minutes not in HR_INTERVALS:
            raise web.HTTPBadRequest(text=f"Intervalo deve ser um destes: {HR_INTERVALS}.")
        return web.json_response({"intervalo_bpm": await watch.set_hr_interval(minutes)})

    @web.middleware
    async def errors(req: web.Request, handler):
        try:
            return await handler(req)
        except web.HTTPException:
            raise
        except Exception as e:  # erros do bleak/WinRT viram texto legível no painel
            traceback.print_exc()
            raise web.HTTPBadGateway(text=f"Falha na conexão com o relógio ({type(e).__name__}: {e}).")

    app = web.Application(middlewares=[errors])
    app.add_routes([
        web.get("/", page),
        web.get("/api/config", config),
        web.get("/api/dados", dados),
        web.post("/api/notificar", notificar),
        web.post("/api/intervalo", intervalo),
    ])
    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", default=WATCH_ADDRESS, help="MAC do relógio (padrão: SOLARIS_WATCH_ADDRESS do .env)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--timeout", type=float, default=15)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    url = f"http://localhost:{args.port}"
    print(f"Painel em {url}  (Ctrl+C para sair)")
    if not args.no_browser:
        webbrowser.open(url)
    web.run_app(make_app(Watch(args.address, args.timeout)), host="127.0.0.1", port=args.port, print=None)


if __name__ == "__main__":
    main()
