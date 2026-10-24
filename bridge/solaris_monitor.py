"""Sessão de monitoramento de saúde ao vivo (usada pelo painel).

Mantém a conexão BLE aberta pela sessão inteira, liga os sensores de saúde no máximo, inicia um treino livre
(é o único modo em que o relógio atualiza o batimento a cada ~15 s) e junta três fontes:
- pushes no canal 8004 (cmd 0xE0 saúde, 0xE3 bateria, 0xE4 treino, 0xD8 eventos);
- consulta do último batimento (0x62) a cada 5 s;
- histórico 0xBE de SpO2, estresse e passos dos últimos minutos, a cada 60 s.
No fim (tempo esgotado, parada manual ou erro) restaura tudo que foi alterado e salva a sessão em JSON.
"""

import asyncio
import json
import time
from pathlib import Path

from bleak import BleakClient

from solaris_notify import SET, send
from solaris_read import DATA1_READ, EOF, SOF, Link, find, le_int, parse_tlv

DATA2_READ = "00008004-0000-1000-8000-00805f9b34fb"  # pushes do relógio (o SDK só trata eventos neste canal)
SESSIONS_DIR = Path(__file__).with_name("sessoes")
SWITCH_BITS = (16, 18, 21, 26, 29)  # FC em tempo real, monitor de FC, cansaço, estresse, SpO2 contínuo
WORKOUT_FREE = 22
MAX_RECONNECTS = 20
HIST_STEPS, HIST_STRESS, HIST_SPO2 = 1, 5, 6


class Monitor:
    def __init__(self, address: str | None, timeout: float) -> None:
        self.address = address
        self.timeout = timeout
        self.task: asyncio.Task | None = None
        self.stop_event = asyncio.Event()
        self.listeners: set[asyncio.Queue] = set()
        self.session: dict | None = self._load_last()

    @staticmethod
    def _load_last() -> dict | None:
        """Última sessão salva, para a página mostrar algo logo depois de o painel reiniciar."""
        files = sorted(SESSIONS_DIR.glob("sessao-*.json"))
        try:
            return json.loads(files[-1].read_text(encoding="utf-8")) if files else None
        except (OSError, ValueError):
            return None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    # ---- eventos para o navegador

    def emit(self, kind: str, **data) -> None:
        event = {"tipo": kind, "s": round(time.time() - self.session["inicio"], 1), **data}
        self.session["eventos"].append(event)
        for q in self.listeners:
            q.put_nowait(event)

    def summary(self) -> dict:
        s = self.session
        if not s:
            return {"estado": "parado"}
        return {k: v for k, v in s.items() if k != "eventos"} | {"eventos": s["eventos"]}

    # ---- controle

    def start(self, minutes: int, mode: str = "completo") -> None:
        """Modos: "completo" = SpO2 e estresse a cada ~3 min e batimento a cada ~1 min (sem treino, que bloqueia SpO2);
        "batimento" = treino livre, batimento a cada ~5 s, sem SpO2/estresse."""
        self.stop_event.clear()
        self.drops = 0
        now = time.time()
        self.session = {"estado": "conectando", "inicio": now, "fim_previsto": now + minutes * 60, "modo": mode,
                        "minutos": minutes, "eventos": [], "erro": None, "arquivo": None}
        self.task = asyncio.create_task(self._run())

    def stop(self) -> None:
        self.stop_event.set()

    async def _set(self, client: BleakClient, link: Link, cmd: int, payload: bytes) -> bool:
        pkt = bytes([SOF, cmd, SET]) + len(payload).to_bytes(2, "little") + payload + bytes([EOF])
        ack = await send(client, link, pkt)
        return bool(ack and len(ack) >= 7 and ack[1] == 0x01 and ack[5] == cmd and ack[6] == 0)

    async def _get(self, link: Link, cmd: int, payload: bytes) -> bytes:
        bodies = await link.request(cmd, payload)
        return bodies[0] if bodies else b""

    def _on_push(self, _char, data: bytearray) -> None:
        b = bytes(data)
        if len(b) < 7 or b[0] != SOF:
            return
        cmd, tlv = b[1], dict(parse_tlv(b[5:-1]))
        if cmd == 0xE0:
            if tlv.get(1):
                self.emit("bpm", valor=tlv[1][0], fonte="push")
            if tlv.get(2):
                self.emit("estresse", valor=tlv[2][0], fonte="push")
            if tlv.get(3):
                self.emit("spo2", valor=tlv[3][0], fonte="push")
            if tlv.get(17) and len(tlv[17]) >= 5:  # resultado da medição sob demanda: [spo2][epoch 4B]
                self.emit("spo2", valor=tlv[17][0], fonte="medição", t=le_int(tlv[17][1:5]))
            if tlv.get(11) and len(tlv[11]) >= 12:
                v = tlv[11]
                self.emit("atividade", passos=le_int(v[0:4]), metros=le_int(v[4:8]), kcal=le_int(v[8:12]) / 1000)
            if tlv.get(12):
                self.emit("bateria", valor=tlv[12][0])
        elif cmd == 0xE3 and len(b) >= 8:
            self.emit("bateria", valor=b[6], carregando=b[5] == 1)
        elif cmd == 0xE4 and tlv.get(7) and tlv[7][0]:
            self.emit("bpm", valor=tlv[7][0], fonte="treino")
        elif cmd == 0xD8 and tlv.get(0):
            names = {0x09: "falha ao medir FC", 0x0C: "treino iniciado", 0x0D: "treino encerrado",
                     0x0E: "medição de SpO2 iniciada", 0x0F: "medição de SpO2 encerrada"}
            self.emit("aviso", texto=names.get(tlv[0][0], f"evento {tlv[0][0]:02X}"))

    async def _emit_last_known(self, link: Link) -> None:
        """Última leitura das 3 horas anteriores, para a página não começar vazia (não entra nas estatísticas)."""
        end = int(self.session["inicio"])
        for kind, field, name in ((HIST_SPO2, 11, "spo2"), (HIST_STRESS, 10, "estresse")):
            points = [p for p in await link.history(kind, end - 3 * 3600, end) if p.get(field)]
            if points:
                self.emit(name, valor=points[-1][field], fonte="anterior", t=points[-1]["t"])
        b = await self._get(link, 0x62, b"\x01")
        if len(b) >= 7:
            self.emit("bpm", valor=b[6], fonte="anterior", t=le_int(b[2:6]))

    async def _poll_history(self, link: Link, seen: dict, since: int) -> None:
        end = int(time.time())
        bat = await self._get(link, 0x08, b"\x01")  # o push de bateria só vem quando o nível muda
        if len(bat) >= 2:
            self.emit("bateria", valor=bat[1], carregando=bat[0] == 1)
        for kind, field, name in ((HIST_SPO2, 11, "spo2"), (HIST_STRESS, 10, "estresse")):
            for p in await link.history(kind, since, end):
                if p.get(field) and p["t"] not in seen[name]:
                    seen[name].add(p["t"])
                    self.emit(name, valor=p[field], fonte="histórico", t=p["t"])
        steps = await link.history(HIST_STEPS, int(self.session["inicio"]), end)
        if steps:
            self.emit("passos", total=sum(p.get(3, 0) for p in steps), kcal=sum(p.get(4, 0) for p in steps) / 1000,
                      metros=sum(p.get(5, 0) for p in steps))

    async def _run(self) -> None:
        s = self.session
        saved: dict = {}
        state = {"seen": {"spo2": set(), "estresse": set()}, "last_bpm": None, "next_history": 0.0}
        try:
            while not await self._connection(saved, state):
                pass
        except Exception as e:  # desistiu de reconectar: tenta ao menos restaurar numa conexão nova
            s["erro"] = str(e) or type(e).__name__
            self.emit("erro", texto=s["erro"])
            if saved:
                await self._restore_reconnecting(saved)
        finally:
            s["estado"] = "concluido" if not s["erro"] else "erro"
            s["encerrado_em"] = time.time()
            self.emit("fim")
            self._save()

    def _over(self) -> bool:
        s = self.session
        return s["estado"] != "conectando" and (time.time() >= s["fim_previsto"] or self.stop_event.is_set())

    async def _connection(self, saved: dict, state: dict) -> bool:
        """Uma conexão da sessão. Devolve True quando a sessão acabou e foi restaurada;
        False se a conexão caiu e vale tentar de novo (até MAX_RECONNECTS vezes)."""
        s = self.session
        try:
            dev = None
            for attempt in range(3):  # a busca BLE no Windows às vezes não acha o relógio na primeira
                dev = await find(self.address, max(self.timeout, 30))
                if dev:
                    break
                self.emit("aviso", texto=f"relógio não encontrado (tentativa {attempt + 1} de 3)")
            if not dev:
                raise RuntimeError("Relógio não encontrado. Desligue o Bluetooth do celular e tente de novo.")
            async with BleakClient(dev) as client:
                link = Link(client, raw=False)
                await client.start_notify(DATA1_READ, link.on_notify)
                await client.start_notify(DATA2_READ, self._on_push)

                if not saved:  # só na primeira conexão: estado a restaurar no fim
                    saved["fc"] = (await self._get(link, 0x5C, b"\x00"))[:1]
                    saved["spo2"] = dict(parse_tlv(await self._get(link, 0x63, b"\x01"))).get(1, b"")[:1]
                    saved["estresse"] = dict(parse_tlv(await self._get(link, 0x63, b"\x02"))).get(2, b"")[:1]
                    mask = await self._get(link, 0x90, b"\x00")
                    saved["chaves"] = le_int(mask[:4]) if len(mask) >= 4 else None
                    s["estado_original"] = {k: (v.hex() if isinstance(v, bytes) else v) for k, v in saved.items()}
                    self.emit("aviso", texto="estado atual dos sensores guardado")

                full = s["modo"] == "completo"
                if not self._over():
                    await self._set(client, link, 0x19, b"\x03\x01\x01")  # app em primeiro plano
                    for bit in SWITCH_BITS:
                        await self._set(client, link, 0x90, bytes([1, bit, 1]))
                    await self._set(client, link, 0x5C, b"\x01")
                    # a cada 3 min (testado); religar as chaves faz o relógio reagendar já com o novo intervalo
                    minutes = 3 if full else 1
                    await self._set(client, link, 0x63, bytes([1, 1, minutes]))
                    await self._set(client, link, 0x63, bytes([2, 1, minutes]))
                    if full:
                        for bit in (29, 21, 26):
                            await self._set(client, link, 0x90, bytes([1, bit, 0]))
                            await self._set(client, link, 0x90, bytes([1, bit, 1]))
                    running = dict(parse_tlv(await self._get(link, 0x41, b"\x00"))).get(0, b"\x00")[:1] == b"\x01"
                    if not full and not running and await self._set(client, link, 0x6E, bytes([1, 1, 1, 0, 1, WORKOUT_FREE])):
                        saved["treino"] = True
                    if s["estado"] == "conectando":
                        s["estado"] = "monitorando"
                        s["inicio"] = time.time()
                        s["fim_previsto"] = s["inicio"] + s["minutos"] * 60
                        self.emit("aviso", texto="sensores no máximo; SpO2 e estresse a cada 3 min" if full
                                  else "sensores no máximo e treino livre iniciado (batimento a cada ~5 s)")
                        self.emit("inicio", fim_previsto=s["fim_previsto"])
                        await self._emit_last_known(link)
                    else:
                        self.emit("aviso", texto="reconectado; sensores religados")

                since = int(s["inicio"])  # o que veio antes já foi enviado como "anterior"
                while not self._over():
                    if not client.is_connected:
                        raise RuntimeError("A conexão com o relógio caiu.")
                    b = await self._get(link, 0x62, b"\x01")
                    if len(b) >= 7 and (le_int(b[2:6]), b[6]) != state["last_bpm"]:
                        state["last_bpm"] = (le_int(b[2:6]), b[6])
                        self.emit("bpm", valor=b[6], fonte="consulta", t=state["last_bpm"][0])
                    if time.time() >= state["next_history"]:
                        await self._poll_history(link, state["seen"], since)
                        state["next_history"] = time.time() + 60
                    if full and time.time() >= state.get("next_spo2", 0):
                        # medição sob demanda: o resultado chega por push 0xE0 TLV 17 em ~20 s
                        if await self._set(client, link, 0x1A, b"\x27"):
                            self.emit("aviso", texto="medindo SpO2")
                        state["next_spo2"] = time.time() + 180
                    try:
                        await asyncio.wait_for(self.stop_event.wait(), 5)
                    except asyncio.TimeoutError:
                        pass

                s["estado"] = "restaurando"
                await self._poll_history(link, state["seen"], since)
                await self._restore(client, link, saved)
                return True
        except Exception as e:
            self.drops = getattr(self, "drops", 0) + 1
            if self.drops > MAX_RECONNECTS:
                raise RuntimeError(f"A conexão caiu {self.drops} vezes; sessão encerrada.") from e
            self.emit("aviso", texto=f"conexão caiu ({e or type(e).__name__}); reconectando ({self.drops}/{MAX_RECONNECTS})…")
            await asyncio.sleep(3)
            return False

    async def _restore(self, client: BleakClient, link: Link, saved: dict) -> None:
        if saved.get("treino"):
            await self._set(client, link, 0x6E, bytes([1, 1, 3]))
        await self._set(client, link, 0x1A, b"\x28")  # encerra medição de SpO2 que esteja em curso
        if saved.get("fc"):
            await self._set(client, link, 0x5C, saved["fc"])
        if saved.get("spo2"):
            await self._set(client, link, 0x63, b"\x01\x01" + saved["spo2"])
        if saved.get("estresse"):
            await self._set(client, link, 0x63, b"\x02\x01" + saved["estresse"])
        if saved.get("chaves") is not None:
            for bit in SWITCH_BITS:
                await self._set(client, link, 0x90, bytes([1, bit, saved["chaves"] >> bit & 1]))
        self.emit("aviso", texto="sensores restaurados ao estado anterior")

    async def _restore_reconnecting(self, saved: dict) -> None:
        try:
            dev = await find(self.address, self.timeout)
            if dev:
                async with BleakClient(dev) as client:
                    link = Link(client, raw=False)
                    await client.start_notify(DATA1_READ, link.on_notify)
                    await self._restore(client, link, saved)
        except Exception as e:
            self.emit("erro", texto=f"Não consegui restaurar os sensores: {e}. Use a aba de ajustes.")

    def _save(self) -> None:
        SESSIONS_DIR.mkdir(exist_ok=True)
        path = SESSIONS_DIR / time.strftime("sessao-%Y%m%d-%H%M%S.json", time.localtime(self.session["inicio"]))
        self.session["arquivo"] = path.name
        path.write_text(json.dumps(self.session, ensure_ascii=False, indent=1), encoding="utf-8")
