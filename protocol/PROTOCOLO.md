# Solaris — protocolo do Haylou Solar Ultra (LS23)

Fonte: Haylou Fun **V4.0.7** (`com.yingsheng.hayloufun`, versionCode 267), extraído de um celular Android via adb
e decompilado com jadx 1.5.1.

## Relógio
- Nome BLE: `HAYLOU Solar Ultra`, dual-mode (BR/EDR p/ chamadas + BLE p/ dados)
- Código interno: **LS23** (projectNo `LS23002`); o app mapeia `Watch162` -> `LS23` (`CommonUtil.java:94`)
- Chip: **SiFli** (SDK tem pacote `slifi/` com DFU e watchface; UUIDs `...6473-5f696c666973` = "sifli_sd" em ASCII invertido)

## Arquitetura do app
O Haylou Fun é uma "casca" com SDKs de vários fabricantes de relógio, escolhido pelo modelo:
- `com.huawo.sdk.bluetoothsdk` — **usado pelo LS23** (e LS16/LS18/LS20/...)
- outros: topstep/fitcloud, crrepa (MoYoung), jieli, yc nadal, apex, realsil (Realtek)
- Ponte app<->SDK do LS23: `com/liesheng/haylou/service/watch/huawo/HuaWoWatchControlHelper.java`

## GATT (Huawo SDK) — `core/model/BleUUID.java`
| Uso | Serviço | Write | Notify/Read |
|---|---|---|---|
| Dados (canal 1) | `00006006-...` | `00008001-...` | `00008002-...` |
| Dados (canal 2) | | `00008003-...` | `00008004-...` |
| Dados (canal 3) | | `00008005-...` | `00008006-...` |
| Alternativo (CY) | `0000fd35-...` | | |
| OTA | `00001530-...` | `00001531-...` | `00001532-...` |
| WL | `00001630-...` | `00001631-...` | `00001632-...` |

(todos com sufixo `-0000-1000-8000-00805f9b34fb`)

## Formato do pacote (byte protocol, não protobuf)
```
0x6F | cmd | sub | len (2 bytes, LE) | payload (len bytes) | 0x8F
```
- Resposta: mesmo `cmd` no byte 1; payload útil = bytes 5 .. fim-1 (`ops/base/GetTask.java`)
- `cmd == 0xBF` = resposta em várias partes (índice nos bytes 8-9, total nos 10-11)

Exemplo — **ler bateria** (`ops/GetBattery.java`):
```
envia:   6F 08 70 01 00 00 8F
resposta: payload[0] = % bateria
```

## Comandos
Catálogo completo, com risco de cada um e o que o app usa no LS23: **[COMANDOS.md](COMANDOS.md)**.

`com/huawo/sdk/bluetoothsdk/interfaces/ops/` tem **271** classes, uma por comando
(GetBattery, GetDeviceInfo, GetAlarms, EditAlarm, GetContacts, DeleteWatchface, CustomWatchfaceHandler, EnterOta...).
Cada uma tem `sendBytes()` (o que envia) e `parse()` (como lê a resposta).

## Mostradores
- Personalizados: `ops/CustomWatchfaceHandler.java` -> obtém endereço OTA, envia widgets e transfere o arquivo via `BluetoothSDK.ota(...)`
- Formato do arquivo SiFli: `slifi/watchface/`
- UI do app: `com/liesheng/module_device/dial/`

## Confirmado no relógio (2026-10-23, `bridge/solaris_read.py` pelo BLE do PC)
- Respondeu a todos os Get* **sem handshake/bind** — basta conectar e assinar `8002`
- Resposta usa `sub = 0x80` (pedido usa `0x70`)
- Serviços reais: `6006` (8001-8004), SiFli `...6473-5f696c666973`, `56ff` (34f1/34f2) e GAP/GATT
- Firmware V1.0.9R0.1T0.3H0.0B44, protocol_version 3, watchface_version 18
- TLV 18 = `CC1165W_V3.51.1.1165B14964` (ASCII), TLV 28 = `LS23…` (provável nº de série, 17 caracteres)
- Mostrador atual: `JW_LS20WF0190` (mostradores do LS20 servem no LS23)

## Histórico de saúde (confirmado)
Tudo em `cmd 0xBE`, intervalo = início/fim em epoch segundos (4 bytes LE cada):
1. Contagem: payload `00 <tipo> 08 <início> <fim>` -> TLV `<tipo> <len> <nº de pacotes>`
2. Dados: payload `<tipo> <tipo> 08 <início> <fim>` -> N pacotes `6F BE 80 ...`
- tipo 1 = passos, 3 = sono, 4 = batimentos (2 = atividade, 5 = estresse, 6 = SpO2)
- Corpo = pares varint (campo, valor). Campo 0 = timestamp absoluto, 1 = +s, 2 = -s (abrem novo ponto)
- Passos: 3 = passos, 4 = calorias (cal, /1000 = kcal), 5 = distância (m), 6 = duração
- Batimentos: 7 = bpm (a cada ~10 min)
- Sono: 13 = estado (0 profundo, 1 leve, 2 acordado, 5 REM, 16 entrou, 17 saiu)
- `GetNewestHeartrate` é `6F 62 70 01 00 01 8F` (**confirmado**): resposta `01 05 <epoch 4B LE> <bpm>`.
  O `0x90` usado antes é a família FeatureSwitch — os 4 bytes `f8 .. 20 20` eram a máscara de chaves de recurso

## Configurações (SET)
- Pedido SET usa `sub = 0x71`; o relógio confirma com `6F 01 81 02 00 <cmd> <status> 8F` (status 00 = OK)
- Intervalo de batimentos: GET `6F 5C 70 01 00 00 8F`, SET `6F 5C 71 01 00 <min> 8F`
  - App oficial só usa 10 (auto) ou 0 (desligado); **3 foi aceito** em 2026-10-23 (lido de volta = 3)
  - **Confirmado no histórico:** medições às 19:15, 19:18, 19:21, 19:24 (a cada 3 min); antes de 10 em 10 até 18:58

## Notificações (PushMessage, confirmado 2026-10-23)
```
6F 79 71 <len 2B> | <tam 2B> <id 4B LE> 00 | TLV(1, tipo) TLV(2, título) TLV(3, conteúdo) TLV(4, "yyyyMMddTHHmmss") | 8F
TLV = <tamanho+1 2B LE> <tag> <dados UTF-8>
```
- Título até 90 bytes, conteúdo até 357 bytes; tipo = SocialType (1 SMS, 10 WhatsApp, 26 Telegram, 49 Outro...)
- MTU negociado no Windows: 527 (cabe em um write só)
- Confirmação: `6F 01 81 02 00 79 00 8F`
- Script: `bridge/solaris_notify.py`

## Próximos passos
1. Script Python (bleak) só-leitura: conectar no `00006006`, assinar `00008002`, enviar GetBattery / GetDeviceInfo
2. Capturar HCI snoop no celular trocando mostrador, comparar com `slifi/watchface`
