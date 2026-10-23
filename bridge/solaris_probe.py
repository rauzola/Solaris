# /// script
# requires-python = ">=3.11"
# dependencies = ["bleak>=0.22"]
# ///
"""Sonda só-leitura: envia todos os GET conhecidos do LS23 e guarda as respostas brutas.

Só monta pacotes com sub 0x70 (GET); qualquer outro sub é recusado antes de escrever.
A lista vem de protocol/COMANDOS.md. Respostas vão para um JSON (padrão: research/sonda.json).

Uso:
    uv run solaris_probe.py
    uv run solaris_probe.py --saida ../research/sonda.json --raw
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from bleak import BleakClient

from solaris_read import DATA1_READ, DATA1_WRITE, DATA_SERVICE, GET, WATCH_ADDRESS, Link, find, frame

# (nome, cmd, payload hex)
PROBES = [
    ("GetDeviceID", 0x02, "00"),
    ("GetDeviceType", 0x03, "00"),
    ("GetFirmwareVersion", 0x03, "07"),
    ("GetDevicePasscodeAndSwitch", 0x03, "12"),  # a senha é mascarada no JSON
    ("GetFaceScreen", 0x05, "00"),
    ("GetContactsNum", 0x06, "00"),
    ("GetBrightness", 0x07, "00"),
    ("GetBatteryAndState", 0x08, "01"),
    ("GetVolumn", 0x09, "00"),
    ("GetLanguage", 0x0B, "00"),
    ("GetDeviceSupportedLanguages", 0x0B, "01"),
    ("GetUnit", 0x0C, "00"),
    ("GetTimeFormat", 0x0F, "01"),
    ("GetWatchfaceID", 0x0F, "02"),
    ("GetWatchfaceName", 0x11, "01"),
    ("GetWatchfaceNameList", 0x11, "00"),
    ("GetUpgradeStatus", 0x16, "00"),
    ("GetBTConnectionState", 0x19, "02"),
    ("GetWatchfaceIDs", 0x1E, "00"),
    ("GetWeatherUnit", 0x22, "01"),
    ("GetDisplayDuration", 0x25, "00"),
    ("GetUserInfo", 0x30, "00"),
    ("GetWearHabit", 0x31, "00"),
    ("GetWorldClockCityIDs", 0x40, "00"),
    ("GetWorkoutState", 0x41, "00"),
    ("GetSleepState", 0x41, "01"),
    ("GetDeviceFeatures", 0x41, "02"),
    ("GetMusicAvailableStorage", 0x41, "030D"),
    ("GetAlbumIdleStorage", 0x41, "04"),
    ("GetGpsStatus", 0x41, "05060C"),
    ("GetAlbumFilesCount", 0x41, "07"),
    ("GetDeviceProtocolVersion", 0x41, "09"),
    ("GetDeviceWatchfaceVersion", 0x41, "0A"),
    ("GetSN", 0x41, "0B"),
    ("GetMapAvailableStorage", 0x41, "0E"),
    ("GetAllElectronicCard", 0x42, "00"),
    ("GetVersionForLS", 0x43, "01"),
    ("GetGoal", 0x50, "00"),
    ("GetActivityNum", 0x52, "00"),
    ("GetHeartrateMonitorInterval", 0x5C, "00"),
    ("GetHeartrateAlarm", 0x5D, "00"),
    ("GetSedentaryReminderForLS16", 0x5E, "00"),
    ("GetNewestHeartrate", 0x62, "01"),
    ("GetSpO2MonitorInterval", 0x63, "01"),
    ("GetStressMonitorInterval", 0x63, "02"),
    ("GetSpO2Alert", 0x63, "03"),
    ("GetPlateauCare", 0x63, "0D"),
    ("GetBodyState", 0x64, "03000000"),
    ("GetWorkoutCount", 0x66, "00"),
    ("GetFeatureSwitches", 0x90, "00"),
    ("GetUID", 0x93, "00"),
    ("GetBindState", 0x94, "00"),
    ("GetAllDrinkWaterRecords", 0x9B, "0000"),
    ("GetReminderAndAlarmNum", 0x9C, "00"),
    ("GetReminders", 0x9C, "02"),
    ("GetAlarms", 0x9C, "03"),
    ("GetDrinkWaterReminder", 0x9D, "00"),
    ("GetWashHandReminder", 0x9D, "01"),
    ("GetSocialAppSwitches", 0x9E, "00"),
    ("GetSocialApps", 0x9E, "01"),
    ("GetQuickReplyCount", 0xDA, "00"),
    ("GetQuickReplies", 0xDB, "00"),
]

# histórico 0xBE ainda não lido pelos outros scripts: (nome, tipo)
HISTORY = [("atividade", 2), ("estresse", 5), ("spo2", 6), ("treinos", 7)]


async def get_all(client: BleakClient, link: Link, cmd: int, payload: bytes, raw: bool) -> list[str]:
    """Envia um GET e junta todos os pacotes de resposta do mesmo cmd (ou páginas 0xBF) por ~0,6 s após o primeiro."""
    pkt = frame(cmd, payload)
    if pkt[2] != GET:  # trava: esta sonda nunca grava
        raise RuntimeError(f"pacote não é GET: {pkt.hex(' ')}")
    link.buf.clear()
    link.packets.clear()
    link.got.clear()
    if raw:
        print(f"    -> {pkt.hex(' ')}")
    await client.write_gatt_char(DATA1_WRITE, pkt, response=False)
    out: list[str] = []
    timeout = 3.0
    while True:
        try:
            await asyncio.wait_for(link.got.wait(), timeout)
        except asyncio.TimeoutError:
            return out
        link.got.clear()
        while link.packets:
            p = link.packets.pop(0)
            if p[1] in (cmd, 0xBF):
                out.append(p.hex(" "))
        timeout = 0.6


def mask_passcode(packets: list[str]) -> list[str]:
    """Troca os dígitos da senha por '*', mantendo cabeçalho e flag ligado/desligado."""
    masked = []
    for h in packets:
        b = bytes.fromhex(h)
        body = b[5:-1]
        if len(body) > 2:
            body = body[:2] + b"*" * (len(body) - 2)
        masked.append((b[:5] + body + b[-1:]).hex(" "))
    return masked


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", default=WATCH_ADDRESS)
    ap.add_argument("--saida", default=str(Path(__file__).resolve().parent.parent / "research" / "sonda.json"))
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--timeout", type=float, default=15)
    args = ap.parse_args()

    dev = await find(args.address, args.timeout)
    if not dev:
        print("Relógio não encontrado. Desligue o Bluetooth do celular e tente de novo.")
        return 1
    print(f"Conectado a {dev.name} [{dev.address}]")

    result: dict = {"lido_em": int(time.time()), "comandos": {}, "historico": {}}
    async with BleakClient(dev) as client:
        if not client.services.get_service(DATA_SERVICE):
            print("Serviço 0x6006 não encontrado.")
            return 1
        link = Link(client, args.raw)
        await client.start_notify(DATA1_READ, link.on_notify)

        for name, cmd, payload in PROBES:
            packets = await get_all(client, link, cmd, bytes.fromhex(payload), args.raw)
            if name == "GetDevicePasscodeAndSwitch":
                packets = mask_passcode(packets)
            result["comandos"][name] = {"cmd": f"{cmd:02X}", "payload": payload, "respostas": packets}
            first = packets[0] if packets else "(sem resposta)"
            print(f"{name:30} {cmd:02X}/{payload:8} {len(packets)} pct  {first[:80]}")

        end = int(time.time())
        start = end - 7 * 86400
        for name, kind in HISTORY:
            points = await link.history(kind, start, end)
            result["historico"][name] = points
            print(f"histórico {name:21} {len(points)} pontos  {points[-1] if points else ''}")

    out = Path(args.saida)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nRespostas salvas em {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
