# /// script
# requires-python = ">=3.11"
# dependencies = ["bleak>=0.22"]
# ///
"""Envia uma notificação personalizada do PC para o Haylou Solar Ultra (LS23).

Formato de interfaces/ops/PushMessage.java (sendBytesLegacy):
    6F 79 71 <len> | <tam> <id 4B> 00 | TLV tipo | TLV título | TLV conteúdo | TLV hora | 8F
cada TLV = <tamanho 2B LE> <tag> <dados>; tags 1=tipo, 2=título, 3=conteúdo, 4=hora ("yyyyMMddTHHmmss").

Uso:
    uv run solaris_notify.py "Título" "Mensagem"
    uv run solaris_notify.py "Pedido #123" "Novo pedido de R$ 250,00" --tipo whatsapp
"""

import argparse
import asyncio
import sys
import time
from datetime import datetime

from bleak import BleakClient

from solaris_read import DATA1_READ, DATA1_WRITE, EOF, SOF, WATCH_ADDRESS, Link, find

CMD_PUSH_MESSAGE = 0x79
SET = 0x71
TITLE_MAX, CONTENT_MAX = 90, 357

# subconjunto de interfaces/ops/models/SocialType.java (define o ícone no relógio)
TYPES = {
    "sms": 1, "social": 2, "email": 3, "calendario": 4, "wechat": 7, "whatsapp": 10,
    "facebook": 12, "gmail": 14, "messenger": 15, "instagram": 16, "twitter": 17,
    "linkedin": 18, "telegram": 26, "slack": 33, "youtube": 37, "outro": 49, "outlook": 57,
}


def truncate_utf8(text: str, limit: int) -> bytes:
    data = text.encode("utf-8")
    if len(data) <= limit:
        return data
    return data[:limit].decode("utf-8", "ignore").encode("utf-8")


def tlv(tag: int, data: bytes) -> bytes:
    return (len(data) + 1).to_bytes(2, "little") + bytes([tag]) + data


def build_message(msg_id: int, kind: int, title: str, content: str, when: datetime) -> bytes:
    body = (
        tlv(1, bytes([kind]))
        + tlv(2, truncate_utf8(title, TITLE_MAX))
        + tlv(3, truncate_utf8(content, CONTENT_MAX))
        + tlv(4, when.strftime("%Y%m%dT%H%M%S").encode())
    )
    inner = msg_id.to_bytes(4, "little") + b"\x00" + body
    payload = len(inner).to_bytes(2, "little") + inner
    return bytes([SOF, CMD_PUSH_MESSAGE, SET]) + len(payload).to_bytes(2, "little") + payload + bytes([EOF])


async def send(client: BleakClient, link: Link, pkt: bytes) -> bytes | None:
    """Escreve em pedaços do tamanho do MTU e espera a confirmação 6F 01 81 .. 79 <status>."""
    link.buf.clear()
    link.packets.clear()
    link.got.clear()
    chunk = max(20, client.mtu_size - 3)
    for i in range(0, len(pkt), chunk):
        await client.write_gatt_char(DATA1_WRITE, pkt[i : i + chunk], response=False)
        await asyncio.sleep(0.02)
    try:
        await asyncio.wait_for(link.got.wait(), 5)
    except asyncio.TimeoutError:
        return None
    return link.packets[0] if link.packets else None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("titulo")
    ap.add_argument("mensagem")
    ap.add_argument("--tipo", default="sms", choices=sorted(TYPES), help="ícone mostrado no relógio")
    ap.add_argument("--address", default=WATCH_ADDRESS, help="MAC do relógio (padrão: SOLARIS_WATCH_ADDRESS do .env)")
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--timeout", type=float, default=30)
    args = ap.parse_args()

    dev = await find(args.address, args.timeout)
    if not dev:
        print("Relógio não encontrado. Desligue o Bluetooth do celular e tente de novo.")
        return 1

    async with BleakClient(dev) as client:
        link = Link(client, args.raw)
        await client.start_notify(DATA1_READ, link.on_notify)
        pkt = build_message(int(time.time()) & 0x7FFFFFFF, TYPES[args.tipo], args.titulo, args.mensagem, datetime.now())
        if args.raw:
            print(f"-> {pkt.hex(' ')}  (MTU {client.mtu_size})")
        ack = await send(client, link, pkt)
        if ack and len(ack) >= 7 and ack[1] == 0x01 and ack[5] == CMD_PUSH_MESSAGE:
            ok = ack[6] == 0
            print("Notificação enviada." if ok else f"Relógio recusou (status {ack[6]}).")
            return 0 if ok else 1
        print(f"Sem confirmação do relógio ({ack.hex(' ') if ack else 'nada recebido'}).")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
