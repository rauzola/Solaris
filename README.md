# Solaris

Controle aberto do smartwatch **Haylou Solar Ultra** (modelo interno LS23), sem depender do app oficial.
O protocolo Bluetooth foi obtido por engenharia reversa do Haylou Fun 4.0.7 e validado no relógio.

> Projeto independente, sem relação com a Haylou. Use por sua conta e risco.

## Estrutura

```
Solaris/
├── protocol/   PROTOCOLO.md — comandos, UUIDs e formatos confirmados
├── bridge/     scripts Python que falam com o relógio pelo Bluetooth do PC
├── research/   material de pesquisa local (APK, código decompilado) — fora do git
├── server/     (planejado) servidor de notificações na VPS
└── app/        (planejado) app Android
```

## O que já funciona

| Função | Script |
|---|---|
| Bateria, info do aparelho, firmware | `bridge/solaris_read.py` |
| Histórico de passos, batimentos e sono | `bridge/solaris_read.py --days N` |
| Notificação personalizada (título + texto, com acentos) | `bridge/solaris_notify.py` |
| Intervalo de medição de batimentos (testado em 3 min) | ver `protocol/PROTOCOLO.md` |

## Como rodar

Requer [uv](https://docs.astral.sh/uv/) (baixa o Python e o `bleak` sozinho) e Bluetooth no PC.
O relógio só aceita **uma conexão por vez**: desligue o Bluetooth do celular antes.

Copie `.env.example` para `.env` e coloque o MAC do seu relógio em `SOLARIS_WATCH_ADDRESS`
(sem ele, os scripts procuram pelo nome "HAYLOU"). O `.env` fica fora do git.

```bash
cd bridge
uv run solaris_read.py --scan
uv run solaris_read.py
uv run solaris_notify.py "Título" "Mensagem" --tipo whatsapp
```

## Roteiro

1. `server/` — API na VPS (chave de acesso, fila, histórico) + WebSocket para a ponte
2. `bridge/` — ponte no PC conectada ao servidor, repassando notificações ao relógio
3. `app/` — app Android como ponte definitiva + sincronização de saúde
4. Mostradores próprios (formato SiFli, 480×480 redondo)
