# /// script
# requires-python = ">=3.11"
# dependencies = ["bleak>=0.22"]
# ///
"""Leitura (somente GET) do Haylou Solar Ultra (LS23) via BLE.

Protocolo extraído do Haylou Fun 4.0.7 (SDK com.huawo.sdk.bluetoothsdk):
    0x6F | cmd | sub | len (2 bytes LE) | payload | 0x8F
Só envia comandos Get*; nada que altere ou apague dados do relógio.

Uso:
    uv run solaris_read.py                 # info + passos/batimentos/sono de hoje
    uv run solaris_read.py --days 3        # histórico dos últimos 3 dias
    uv run solaris_read.py --scan          # lista dispositivos BLE próximos
    uv run solaris_read.py --raw          # mostra os bytes trocados

O MAC do relógio fica em SOLARIS_WATCH_ADDRESS no .env da raiz (veja .env.example).
"""

import argparse
import asyncio
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from bleak import BleakClient, BleakScanner

def load_env(path: Path = Path(__file__).resolve().parent.parent / ".env") -> None:
    """Carrega KEY=valor do .env da raiz do projeto (sem sobrescrever variáveis já definidas)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()
# MAC do relógio vem do .env (SOLARIS_WATCH_ADDRESS); sem ele, procura pelo nome "HAYLOU"
WATCH_ADDRESS = os.environ.get("SOLARIS_WATCH_ADDRESS") or None

DATA_SERVICE = "00006006-0000-1000-8000-00805f9b34fb"
DATA1_WRITE = "00008001-0000-1000-8000-00805f9b34fb"
DATA1_READ = "00008002-0000-1000-8000-00805f9b34fb"

SOF, EOF, GET = 0x6F, 0x8F, 0x70
CMD_HISTORY = 0xBE  # GetSportNumForLS / GetSportsForLS / GetHeartratesForLS / GetSleepPointsForLS

# (nome, cmd, payload) — tirados de interfaces/ops/Get*.java
COMMANDS = [
    ("GetBatteryAndState", 0x08, 0x01),
    ("GetBindState", 0x94, 0x00),
    ("GetDeviceInfo", 0x03, 0x08),
    ("GetNewestHeartrate", 0x62, 0x01),  # 0x62 (tabela com.crrepa.d.a), não 0x90
]

# Tipos de histórico (byte 5/6 do pedido e tipo TLV na contagem)
HIST_STEPS, HIST_SLEEP, HIST_HR = 1, 3, 4

# Tipos TLV de GetDeviceInfo.parse()
INFO_TYPES = {
    1: "fw_V", 2: "fw_R", 3: "fw_T", 4: "fw_H", 5: "fw_B",
    15: "id", 16: "type", 18: "bt_firmware", 19: "protocol_version", 20: "watchface_version",
    21: "features", 22: "mac", 23: "bind_state", 24: "language",
    25: "supported_languages", 26: "watchface_atual", 27: "battery", 28: "serial", 29: "map",
}

SLEEP_STATES = {0: "profundo", 1: "leve", 2: "acordado", 3: "pré", 5: "REM",
                16: "entrou", 17: "saiu", 18: "preset", 31: "resumo", 32: "atividade"}


def frame(cmd: int, payload: bytes) -> bytes:
    return bytes([SOF, cmd, GET]) + len(payload).to_bytes(2, "little") + payload + bytes([EOF])


def le_int(b: bytes) -> int:
    return int.from_bytes(b, "little")


def parse_tlv(data: bytes) -> list[tuple[int, bytes]]:
    out, i = [], 0
    while i + 2 < len(data):
        t, n = data[i], data[i + 1]
        if n == 0:
            i += 2
            continue
        if i + 2 + n > len(data):
            break
        out.append((t, data[i + 2 : i + 2 + n]))
        i += 2 + n
    return out


def time_range(start: int, end: int) -> bytes:
    return start.to_bytes(4, "little") + end.to_bytes(4, "little")


def read_varint(data: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while i < len(data):
        b = data[i]
        value |= (b & 0x7F) << shift
        i += 1
        if b < 0x80:
            break
        shift += 7
    return value, i


def decode_points(packets: list[bytes]) -> list[dict]:
    """Pares varint (campo, valor). Campos 0/1/2 abrem um novo ponto:
    0 = timestamp absoluto, 1 = +segundos, 2 = -segundos (relativos ao último 0)."""
    points: list[dict] = []
    for data in packets:
        base, cur, i = 0, None, 0
        while i < len(data):
            field, i = read_varint(data, i)
            if i >= len(data):
                break
            value, i = read_varint(data, i)
            if field in (0, 1, 2):
                ts = value if field == 0 else base + value if field == 1 else base - value
                if field == 0:
                    base = value
                cur = {"t": ts}
                points.append(cur)
            elif cur is not None:
                cur[field] = value
    return points


def history_range(days: int) -> tuple[int, int]:
    """Começa às 18h do dia anterior para pegar a noite de sono inteira; termina agora."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = int((today - timedelta(days=days - 1) - timedelta(hours=6)).timestamp())
    return start, int(time.time())


def fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m %H:%M")


def device_info(body: bytes) -> dict[str, str]:
    """Resposta de GetDeviceInfo (TLV entre o 1º e o último byte) como {campo: texto}."""
    info, fw = {}, {}
    for t, v in parse_tlv(body[1:-1]):
        key = INFO_TYPES.get(t, f"tipo_{t}")
        if key.startswith("fw_"):
            fw[key[3:]] = ".".join(str(x) for x in v)
        elif key in ("id", "type", "watchface_atual", "bt_firmware", "serial"):
            info[key] = v.decode("utf-8", "replace").rstrip(chr(0))
        elif key == "mac":
            info[key] = ":".join(f"{x:02X}" for x in v)
        elif key in ("features", "supported_languages", "map") or t not in INFO_TYPES:
            info[key] = v.hex(" ")
        else:
            info[key] = str(le_int(v))
    if fw:
        info = {"firmware": "".join(f"{k}{fw[k]}" for k in "VRTHB" if k in fw), **info}
    return info


def describe(name: str, body: bytes) -> str:
    if name == "GetBatteryAndState" and len(body) >= 2:
        extra = f", economia={'sim' if len(body) >= 3 and body[2] == 1 else 'não'}"
        return f"carregando={'sim' if body[0] == 1 else 'não'}, bateria = {body[1]}%{extra}"
    if name == "GetBindState":
        return f"bind_state = {le_int(body)}"
    if name == "GetNewestHeartrate" and len(body) >= 7:
        return f"último batimento = {body[6]} bpm às {fmt_ts(le_int(body[2:6]))}"
    if name == "GetNewestHeartrate":
        return f"formato diferente no LS23 (ainda não decodificado): {body.hex(' ')}"
    if name == "GetDeviceInfo" and len(body) > 1:
        return "\n" + "\n".join(f"  {k}: {v}" for k, v in device_info(body).items())
    return body.hex(" ")


class Link:
    def __init__(self, client: BleakClient, raw: bool) -> None:
        self.client = client
        self.raw = raw
        self.buf = bytearray()
        self.packets: list[bytes] = []
        self.got = asyncio.Event()

    def on_notify(self, _char, data: bytearray) -> None:
        if self.raw:
            print(f"    <- {bytes(data).hex(' ')}")
        self.buf += data
        # pode chegar mais de um pacote, ou um pacote em vários pedaços
        while len(self.buf) >= 6 and self.buf[0] == SOF:
            total = le_int(self.buf[3:5]) + 6
            if len(self.buf) < total:
                break
            self.packets.append(bytes(self.buf[:total]))
            del self.buf[:total]
            self.got.set()
        if self.buf and self.buf[0] != SOF:
            self.buf.clear()

    async def request(self, cmd: int, payload: bytes, expect: int = 1, timeout: float = 5) -> list[bytes]:
        """Envia e espera `expect` pacotes de resposta do mesmo cmd; devolve os payloads."""
        self.buf.clear()
        self.packets.clear()
        pkt = frame(cmd, payload)
        if self.raw:
            print(f"    -> {pkt.hex(' ')}")
        await self.client.write_gatt_char(DATA1_WRITE, pkt, response=False)
        bodies: list[bytes] = []
        while len(bodies) < expect:
            self.got.clear()
            if not self.packets:
                try:
                    await asyncio.wait_for(self.got.wait(), timeout)
                except asyncio.TimeoutError:
                    print(f"    (recebi {len(bodies)}/{expect} pacotes antes do tempo limite)")
                    break
            while self.packets:
                p = self.packets.pop(0)
                if p[1] == cmd:
                    bodies.append(p[5:-1])
        return bodies

    async def history(self, kind: int, start: int, end: int) -> list[dict]:
        rng = time_range(start, end)
        # GetSportNumForLS & cia: payload = 0x00 + TLV(tipo, 8, início+fim)
        counts = await self.request(CMD_HISTORY, bytes([0x00, kind, 8]) + rng)
        n = 0
        for t, v in parse_tlv(counts[0]) if counts else []:
            if t == kind:
                n = le_int(v)
        if n == 0:
            return []
        # GetSportsForLS & cia: payload = tipo, tipo, 8, início, fim
        bodies = await self.request(CMD_HISTORY, bytes([kind, kind, 8]) + rng, expect=n, timeout=8)
        return decode_points(bodies)


def show_steps(points: list[dict]) -> None:
    if not points:
        print("  sem registros")
        return
    per_day: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for p in points:
        d = datetime.fromtimestamp(p["t"]).strftime("%d/%m")
        per_day[d][0] += p.get(3, 0)
        per_day[d][1] += p.get(4, 0)
        per_day[d][2] += p.get(5, 0)
    for d, (steps, cal, dist) in per_day.items():
        print(f"  {d}: {steps} passos, {cal / 1000:.0f} kcal, {dist / 1000:.2f} km")
    print(f"  ({len(points)} registros; último às {fmt_ts(points[-1]['t'])}: {points[-1].get(3, 0)} passos)")


def show_hr(points: list[dict]) -> None:
    bpm = [(p["t"], p[7]) for p in points if p.get(7)]
    if not bpm:
        print("  sem registros")
        return
    vals = [b for _, b in bpm]
    print(f"  {len(bpm)} medições, mín {min(vals)} / méd {sum(vals) // len(vals)} / máx {max(vals)} bpm")
    for t, b in bpm[-8:]:
        print(f"    {fmt_ts(t)}  {b} bpm")


def show_sleep(points: list[dict]) -> None:
    pts = [(p["t"], p.get(13)) for p in points if 13 in p]
    if not pts:
        print("  sem registros")
        return
    totals: dict[str, int] = defaultdict(int)
    for (t, s), (t2, _) in zip(pts, pts[1:]):
        if s in (0, 1, 2, 5):
            totals[SLEEP_STATES[s]] += t2 - t
    print(f"  de {fmt_ts(pts[0][0])} até {fmt_ts(pts[-1][0])}")
    for k, secs in totals.items():
        print(f"    {k}: {secs // 3600}h{(secs % 3600) // 60:02d}")


async def find(address: str | None, timeout: float):
    if address:
        return await BleakScanner.find_device_by_address(address, timeout=timeout)
    return await BleakScanner.find_device_by_filter(
        lambda d, ad: "haylou" in (d.name or ad.local_name or "").lower(), timeout=timeout
    )


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", default=WATCH_ADDRESS, help="MAC do relógio (padrão: SOLARIS_WATCH_ADDRESS do .env)")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--days", type=int, default=1, help="dias de histórico (1 = desde ontem 18h)")
    ap.add_argument("--raw", action="store_true", help="mostra os bytes enviados/recebidos")
    ap.add_argument("--timeout", type=float, default=15)
    args = ap.parse_args()

    if args.scan:
        for d, ad in (await BleakScanner.discover(args.timeout, return_adv=True)).values():
            print(f"{d.address}  {ad.rssi:>4} dBm  {d.name or ad.local_name or '-'}")
        return 0

    print("Procurando o relógio...")
    dev = await find(args.address, args.timeout)
    if not dev:
        print("Não encontrado. Desligue o Bluetooth do celular (ou force-pare o Haylou Fun) e tente de novo.")
        return 1
    print(f"Encontrado: {dev.name} [{dev.address}]")

    async with BleakClient(dev) as client:
        if not client.services.get_service(DATA_SERVICE):
            print("Serviço de dados 0x6006 não existe neste relógio; protocolo diferente.")
            return 1
        link = Link(client, args.raw)
        await client.start_notify(DATA1_READ, link.on_notify)

        for name, cmd, payload in COMMANDS:
            print(f"\n[{name}]")
            bodies = await link.request(cmd, bytes([payload]))
            if bodies:
                print(f"  {describe(name, bodies[0])}")

        start, end = history_range(args.days)
        print(f"\nHistórico de {fmt_ts(start)} até {fmt_ts(end)}")

        print("\n[Passos]")
        show_steps(await link.history(HIST_STEPS, start, end))
        print("\n[Batimentos]")
        show_hr(await link.history(HIST_HR, start, end))
        print("\n[Sono]")
        show_sleep(await link.history(HIST_SLEEP, start, end))

        await client.stop_notify(DATA1_READ)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
