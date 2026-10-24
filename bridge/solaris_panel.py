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
    Link, device_info, find, history_range, le_int, parse_tlv,
)
from solaris_notify import CMD_PUSH_MESSAGE, SET, TYPES, build_message, send

PAGE = Path(__file__).with_name("painel.html")
CMD_BATTERY, CMD_DEVICE_INFO, CMD_HR_INTERVAL, CMD_ACK = 0x08, 0x03, 0x5C, 0x01
CMD_NEWEST_HR, CMD_HEALTH_CFG, CMD_ALARMS = 0x62, 0x63, 0x9C
HIST_STRESS, HIST_SPO2 = 5, 6
HR_INTERVALS = (0, 3, 10)  # minutos já testados no relógio (0 = desligado)


# FeatureSwitch (cmd 0x90): índice do bit -> rótulo
SWITCHES = [
    "Alerta de celular longe", "Sincronização automática", "Modo sono", "Sono automático", "Aviso de chamadas",
    "Aviso de chamada perdida", "Aviso de mensagens", "Aviso de redes sociais", "Aviso de e-mail", "Aviso de agenda",
    "Lembrete de sedentarismo", "Economia de energia", "Repetir aviso", "Som de aviso", "Levantar o pulso acende a tela",
    "Aviso de meta atingida", "Batimento em tempo real", "Super alarme", "Monitor de batimento", "Sensor de 3 eixos",
    "Vibração ao girar", "Monitor de cansaço (estresse)", "Vibração ao tocar", "Toque duplo para voltar",
    "Tela sempre ligada", "Modo esporte", "Monitor de estresse", "Aviso de movimento", "Tela desligada com dados",
    "SpO2 contínuo",
]


async def first_body(link: Link, cmd: int, payload: bytes) -> bytes:
    bodies = await link.request(cmd, payload)
    return bodies[0] if bodies else b""


def hhmm(h: int, m: int) -> str:
    return f"{h:02d}:{m:02d}"


def days_from_mask(mask: int) -> list[int]:
    """Máscara de dias do relógio (bit 0 = segunda … bit 6 = domingo) -> [0..6]."""
    return [d for d in range(7) if mask & (1 << d)]


async def raw_alarms(link: Link) -> list[tuple[int, list[tuple[int, bytes]]]]:
    """GetAlarms (9C/03) devolve um pacote por alarme: [índice][id] + TLVs na mesma ordem que o SetAlarms grava."""
    counts = await first_body(link, CMD_ALARMS, b"\x00")
    n = counts[1] if len(counts) >= 2 else 0
    bodies = await link.request(CMD_ALARMS, b"\x03", expect=n) if n else []
    return [(body[1], parse_tlv(body[2:])) for body in bodies if len(body) > 2]


async def read_alarms(link: Link) -> list[dict]:
    """Alarmes legíveis: TLVs 1 nome, 2 dias, 4 hora, 6 ligado."""
    alarms = []
    for alarm_id, tlvs in await raw_alarms(link):
        tlv = dict(tlvs)
        if 4 not in tlv or len(tlv[4]) < 2:
            continue
        alarms.append({
            "id": alarm_id,
            "nome": tlv.get(1, b"").decode("utf-8", "replace"),
            "hora": hhmm(tlv[4][0], tlv[4][1]),
            "dias": days_from_mask(tlv[2][0]) if tlv.get(2) else [],
            "ligado": tlv.get(6, b"\x00")[0] == 1,
        })
    return alarms


async def read_config(link: Link) -> dict:
    """Configurações lidas só com GET; um campo fica None se o relógio não responder."""
    cfg: dict = {}
    b = await first_body(link, 0x07, b"\x00")
    cfg["brilho"] = b[0] if b else None
    b = await first_body(link, 0x25, b"\x00")
    cfg["tela_s"] = le_int(b[:2]) if len(b) >= 2 else None
    b = await first_body(link, 0x0B, b"\x00")
    cfg["idioma"] = b[0] if b else None
    b = await first_body(link, 0x0C, b"\x00")
    cfg["unidade"] = ("métrico" if b[0] == 0 else "imperial") if b else None
    t = dict(parse_tlv(await first_body(link, 0x0F, b"\x01")))
    cfg["formato_hora"] = {1: 24, 2: 12}.get(t[1][0]) if t.get(1) else None

    b = await first_body(link, 0x50, b"\x00")  # u16 a cada 3 bytes: passos/100, kcal, …, minutos ativos (offset 12)
    cfg["metas"] = {"passos": le_int(b[0:2]) * 100, "kcal": le_int(b[3:5]), "minutos": le_int(b[12:14])} if len(b) >= 14 else None
    b = await first_body(link, 0x5E, b"\x00")
    cfg["sedentario"] = {"ligado": bool(b[0] & 0x80), "intervalo_min": b[1],
                         "inicio": hhmm(b[2], b[3]), "fim": hhmm(b[4], b[5])} if len(b) >= 6 else None
    b = await first_body(link, 0x5D, b"\x00")
    cfg["alarme_bpm"] = {"alto": b[0], "baixo": b[1], "ligado": b[2] == 1} if len(b) >= 3 else None

    for key, sub in (("intervalo_spo2_min", 1), ("intervalo_estresse_min", 2)):
        t = dict(parse_tlv(await first_body(link, CMD_HEALTH_CFG, bytes([sub]))))
        cfg[key] = t[sub][0] if t.get(sub) else None

    b = await first_body(link, 0x90, b"\x00")
    mask = le_int(b[:4]) if len(b) >= 4 else None
    cfg["chaves"] = [] if mask is None else [
        {"bit": i, "rotulo": label, "ligado": bool(mask >> i & 1)} for i, label in enumerate(SWITCHES)
    ]
    return cfg


# ---- Ajustes (SET). Só os reversíveis do catálogo; cada um é validado e o relógio precisa confirmar.

LANGUAGES = {0: "Inglês", 6: "Espanhol", 11: "Português"}
SCREEN_SECONDS = (5, 10, 15)  # mesmas opções da tela do app
HEALTH_INTERVALS = (10, 15, 30, 60)  # SpO2/estresse, minutos
SEDENTARY_INTERVALS = (30, 45, 60, 90, 120)


def need(ok: bool, msg: str) -> None:
    if not ok:
        raise web.HTTPBadRequest(text=msg)


def parse_hhmm(value) -> tuple[int, int]:
    try:
        h, m = (int(x) for x in str(value).split(":"))
    except ValueError:
        h = m = -1
    need(0 <= h < 24 and 0 <= m < 60, f"Horário inválido: {value!r} (use HH:MM).")
    return h, m


def tlv_bytes(tlvs: list[tuple[int, bytes]]) -> bytes:
    return b"".join(bytes([t, len(v)]) + v for t, v in tlvs)


async def set_cmd(client: BleakClient, link: Link, cmd: int, payload: bytes) -> None:
    pkt = bytes([SOF, cmd, SET]) + len(payload).to_bytes(2, "little") + payload + bytes([EOF])
    if not acked(await send(client, link, pkt), cmd):
        raise web.HTTPBadGateway(text=f"O relógio recusou o ajuste (cmd {cmd:02X}).")


async def set_alarm(client: BleakClient, link: Link, v: dict) -> None:
    """SetAlarms regrava a lista inteira (um pacote por alarme). Reenvia os TLVs lidos agora, mudando só o alvo."""
    alarms = await raw_alarms(link)
    need(any(a_id == v.get("id") for a_id, _ in alarms), "Alarme não encontrado no relógio.")
    n = len(alarms)
    for a_id, tlvs in alarms:
        if a_id == v["id"]:
            tlvs = dict(tlvs)
            if "ligado" in v:
                tlvs[6] = bytes([1 if v["ligado"] else 0])
            if "hora" in v:
                tlvs[4] = bytes(parse_hhmm(v["hora"]))
            if "dias" in v:
                days = v["dias"]
                need(isinstance(days, list) and days and all(d in range(7) for d in days),
                     "Escolha pelo menos um dia (0 = segunda … 6 = domingo).")
                tlvs[2] = bytes([sum(1 << d for d in set(days))])
            tlvs = sorted(tlvs.items())
        payload = bytes([0x06, (a_id & 0x0F) | ((n << 4) & 0xF0)]) + tlv_bytes(tlvs)
        await set_cmd(client, link, CMD_ALARMS, payload)


async def set_sedentary(client: BleakClient, link: Link, v: dict) -> None:
    need(v.get("intervalo_min") in SEDENTARY_INTERVALS, f"Intervalo deve ser um destes: {SEDENTARY_INTERVALS}.")
    h1, m1 = parse_hhmm(v.get("inicio"))
    h2, m2 = parse_hhmm(v.get("fim"))
    days = 0x7F | (0x80 if v.get("ligado") else 0)  # o app sempre manda todos os dias
    # 0x14 0x00 fixo (como o app); pausa de almoço desligada, 12:00–14:00
    await set_cmd(client, link, 0x5E, bytes([days, v["intervalo_min"], h1, m1, h2, m2, 0x14, 0x00, 0, 12, 0, 14, 0]))


async def set_goals(client: BleakClient, link: Link, v: dict) -> None:
    steps, kcal, minutes = v.get("passos"), v.get("kcal"), v.get("minutos")
    need(isinstance(steps, int) and 1000 <= steps <= 50000 and steps % 100 == 0, "Meta de passos: 1.000 a 50.000, de 100 em 100.")
    need(isinstance(kcal, int) and 50 <= kcal <= 3000, "Meta de kcal: 50 a 3.000.")
    need(isinstance(minutes, int) and 5 <= minutes <= 300, "Meta de minutos ativos: 5 a 300.")
    for goal_type, value in ((0, steps // 100), (1, kcal), (4, minutes)):
        await set_cmd(client, link, 0x50, bytes([goal_type]) + value.to_bytes(2, "little") + b"\x01")


async def apply_setting(client: BleakClient, link: Link, field: str, v) -> None:
    """Lista fechada de campos que o painel pode gravar."""
    if field == "chave":
        need(isinstance(v, dict) and v.get("bit") in range(len(SWITCHES)), "Chave inválida.")
        await set_cmd(client, link, 0x90, bytes([0x01, v["bit"], 1 if v.get("ligado") else 0]))
    elif field == "brilho":
        need(isinstance(v, int) and 0 <= v <= 100, "Brilho: 0 a 100.")
        await set_cmd(client, link, 0x07, bytes([v]))
    elif field == "tela_s":
        need(v in SCREEN_SECONDS, f"Tempo de tela deve ser um destes: {SCREEN_SECONDS}.")
        await set_cmd(client, link, 0x25, v.to_bytes(2, "little"))
    elif field == "idioma":
        need(v in LANGUAGES, f"Idioma deve ser um destes: {LANGUAGES}.")
        await set_cmd(client, link, 0x0B, bytes([v]))
    elif field == "unidade":
        need(v in ("métrico", "imperial"), "Unidade: métrico ou imperial.")
        await set_cmd(client, link, 0x0C, bytes([0 if v == "métrico" else 1]))
    elif field == "formato_hora":
        need(v in (24, 12), "Formato de hora: 24 ou 12.")
        await set_cmd(client, link, 0x0F, bytes([1, 1, 1 if v == 24 else 2]))
    elif field == "metas":
        need(isinstance(v, dict), "Metas inválidas.")
        await set_goals(client, link, v)
    elif field == "sedentario":
        need(isinstance(v, dict), "Lembrete inválido.")
        await set_sedentary(client, link, v)
    elif field == "alarme_bpm":
        need(isinstance(v, dict) and v.get("alto") in range(100, 201) and v.get("baixo") in range(30, 61),
             "Alerta de batimento: alto 100–200, baixo 30–60.")
        await set_cmd(client, link, 0x5D, bytes([v["alto"], v["baixo"], 1 if v.get("ligado") else 0]))
    elif field in ("intervalo_spo2_min", "intervalo_estresse_min"):
        need(v in HEALTH_INTERVALS, f"Intervalo deve ser um destes: {HEALTH_INTERVALS}.")
        sub = 1 if field == "intervalo_spo2_min" else 2
        await set_cmd(client, link, CMD_HEALTH_CFG, bytes([sub, 1, v]))
    elif field == "intervalo_bpm":
        need(v in HR_INTERVALS, f"Intervalo deve ser um destes: {HR_INTERVALS}.")
        await set_cmd(client, link, CMD_HR_INTERVAL, bytes([v]))
    elif field == "alarme":
        need(isinstance(v, dict), "Alarme inválido.")
        await set_alarm(client, link, v)
    else:
        raise web.HTTPBadRequest(text=f"Campo não pode ser alterado pelo painel: {field!r}.")


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
            # 0 = sem leitura naquele horário
            out["spo2"] = [[p["t"], p[11]] for p in await link.history(HIST_SPO2, start, end) if p.get(11)]
            out["estresse"] = [[p["t"], p[10]] for p in await link.history(HIST_STRESS, start, end) if p.get(10)]

            newest = await first_body(link, CMD_NEWEST_HR, b"\x01")
            out["ultimo_bpm"] = {"t": le_int(newest[2:6]), "bpm": newest[6]} if len(newest) >= 7 else None
            out["alarmes"] = await read_alarms(link)
            out["config"] = await read_config(link)

        self.last = out
        return out

    async def notify(self, title: str, content: str, kind: str) -> None:
        async with self.connect() as (_, client, link):
            pkt = build_message(int(time.time()) & 0x7FFFFFFF, TYPES[kind], title, content, datetime.now())
            if not acked(await send(client, link, pkt), CMD_PUSH_MESSAGE):
                raise web.HTTPBadGateway(text="O relógio não confirmou a notificação.")

    async def adjust(self, field: str, value) -> dict:
        """Grava um ajuste e relê do relógio o que ficou valendo."""
        async with self.connect() as (_, client, link):
            await apply_setting(client, link, field, value)
            interval = await first_body(link, CMD_HR_INTERVAL, b"\x00")
            out = {"config": await read_config(link), "alarmes": await read_alarms(link),
                   "intervalo_bpm": interval[0] if interval else None}
        if self.last is not None:
            self.last.update(out)
        return out


def make_app(watch: Watch) -> web.Application:
    async def page(_):
        return web.FileResponse(PAGE)

    async def config(_):
        return web.json_response({
            "tipos": sorted(TYPES), "intervalos": HR_INTERVALS, "ultimo": watch.last,
            "opcoes": {"idiomas": LANGUAGES, "tela_s": SCREEN_SECONDS, "intervalos_saude": HEALTH_INTERVALS,
                       "intervalos_sedentario": SEDENTARY_INTERVALS},
        })

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
        return web.json_response(await watch.adjust("intervalo_bpm", (await req.json()).get("minutos")))

    async def ajuste(req: web.Request):
        try:
            body = await req.json()
        except ValueError:  # JSON inválido ou fora de UTF-8: nada é enviado ao relógio
            raise web.HTTPBadRequest(text="Pedido inválido: envie JSON em UTF-8.")
        return web.json_response(await watch.adjust(str(body.get("campo")), body.get("valor")))

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
        web.post("/api/ajuste", ajuste),
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
