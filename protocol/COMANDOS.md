# Solaris — catálogo de comandos do Haylou Solar Ultra (LS23)

Levantamento completo das **269 classes de comando** do SDK Huawo (`com.huawo.sdk.bluetoothsdk.interfaces.ops`)
no Haylou Fun 4.0.7, cruzado com o que o app oficial realmente envia a um LS23.
Leitura estática do código descompilado (jadx 1.5.1), 2026-10-23. **Só os itens marcados ✅ foram testados no relógio.**

Formato de pacote e detalhes de baixo nível: [PROTOCOLO.md](PROTOCOLO.md).

## Legenda de risco

| Nível | Significado |
|---|---|
| 🟢 Leitura | GET (`sub 0x70`); não altera nada |
| 🟡 Ajuste | SET reversível: dá para ler o valor antes e gravar de volta |
| 🟠 Apaga | remove dados do relógio sem volta (histórico, listas, mostradores) |
| 🔴 Perigoso | reset de fábrica, vínculo, OTA, desligar/reiniciar — pode deixar o relógio inutilizável ou desvinculado |

Coluna **App**: `usa` = o Haylou Fun envia ao LS23; `—` = existe no SDK, mas o app não usa com o LS23 (não testado por ninguém nesse modelo).

## Armadilhas encontradas

- **Duas tabelas de constantes.** 24 ops usam `com.crrepa.d.a` em vez de `com.topstep...protocol.a`.
  Nelas `Z0 = 0x63`, `Y0 = 0x62`, `T0 = 0x5A`. Por isso SpO2, estresse, altitude, GPS, mapa, álbum e agentes de IA são **cmd 0x63**
  (e não 0x91), os apagamentos de FC/HRV/estresse são **0x5A**, e o último batimento é **0x62**.
- **`GetNewestHeartrate` é `6F 62 70 01 00 01 8F`**, não `0x90`. O `0x90` é a família FeatureSwitch;
  isso explica a resposta de 4 bytes "não decodificada" anotada no PROTOCOLO.md.
- **Lista vazia apaga tudo.** `SetAlarms`, `SetReminderEvents`, `SetContacts`, `SetQuickReplies` e `SetAllDrinkWaterRecords`
  com lista vazia viram "apagar todos".
- **Mesmo pacote, nomes diferentes.** `SetSpO2Alert` e `SetSpO2MonitorInterval` geram bytes idênticos (`63 71 | 01 01 v`);
  `StartBind` e `StartSifliBind` também. `SetPhysiologicalCycleReminder` = `SetPhysiologicalPeriodSetting`.
- **Logout no app = reset de fábrica.** Logout, "desvincular" e "restaurar" mandam o mesmo `ResetDevice`.
- **No LS23 o app usa o caminho SiFli** (`projectNo` começa com `LS20`/`LS23`): mostradores e OTA vão por `SFWatchfaceFilePushManager`
  e `SifliDFUService` (zip), não pelas ops Huawo `EnterOta`/`SwitchWatchface`.

## 1. Aparelho e sistema

| cmd | sub | Payload | Comando | Risco | App |
|---|---|---|---|---|---|
| 02 | 70 | `00` | GetDeviceID — ID em UTF-8 | 🟢 | — |
| 03 | 70 | `08` | GetDeviceInfo — TLVs de firmware, MAC, bind, idioma, mostrador, bateria ✅ | 🟢 | — |
| 03 | 70 | `00` / `07` | GetDeviceType / GetFirmwareVersion (string) | 🟢 | usa |
| 08 | 70 | `00` / `01` | GetBattery / GetBatteryAndState (carregando, %, economia) ✅ | 🟢 | usa |
| 19 | 70 | `02` | GetBTConnectionState | 🟢 | — |
| 41 | 70 | `02` | GetDeviceFeatures — 2 inteiros LE de 32 bits (bits na seção 11) | 🟢 | usa |
| 41 | 70 | `09` / `0A` / `0B` | versão de protocolo / versão de mostrador / nº de série | 🟢 | — |
| 41 | 70 | `00` / `01` | treino em andamento? / modo sono ativo? | 🟢 | usa (00) |
| 41 | 70 | `03` `0D` / `0E` / `04` | espaço livre/total de música / de mapa / do álbum (KB) | 🟢 | — |
| 41 | 70 | `05 06 0C` | GetGpsStatus — validade do AGPS, chip GPS | 🟢 | usa |
| 43 | 70 | `01` | GetVersionForLS — versão de firmware (forma usada no LS23) | 🟢 | usa |
| 16 | 70 | `00` | GetUpgradeStatus | 🟢 | — |
| 93 | 70 | `00` | GetUID — UID do dono gravado | 🟢 | usa |
| 94 | 70 | `00` | GetBindState ✅ | 🟢 | — |
| E5 | 71 | `02 len nome` | SetBluetoothName — renomeia o BLE | 🟡 | — |
| E5 | 71 | `01 len nome` | SetDeviceTypeName | 🟡 | — |
| 19 | 71 | TLV 3 = 0/1 | SetClientState — app em primeiro plano | 🟡 | usa |

## 2. Hora, idioma, unidades, tela

| cmd | sub | Payload | Comando | Risco | App |
|---|---|---|---|---|---|
| 04 | 71 | `ano(2) mês dia h m s estilo 00 sinalUTC offH offM` | SetDeviceTime — data, hora e fuso | 🟡 | usa |
| 0B | 70 / 71 | `00` / `v` | Get/SetLanguage (en=0, pt=11, es=6…; tabela em `LanguageUtils`) | 🟡 | usa |
| 0B | 70 | `01` | GetDeviceSupportedLanguages | 🟢 | — |
| 0C | 70 / 71 | `00` / `v` | Get/SetUnit (0 métrico, 1 imperial) | 🟡 | usa |
| 0F | 70 / 71 | `01` / TLV(1,1,v) | Get/SetTimeFormat (1 = 24 h, 2 = 12 h) | 🟡 | usa |
| 22 | 70 / 71 | `01` / `01 v` | Get/SetWeatherUnit (°C/°F) | 🟡 | — |
| 07 | 70 / 71 | `00` / `v` | Get/SetBrightness | 🟡 | — (menu oculto) |
| 25 | 70 / 71 | `00` / `seg(2)` | Get/SetDisplayDuration — tempo de tela | 🟡 | — (menu oculto) |
| 09 | 70 / 71 | `00` / `v` | Get/SetVolumn | 🟡 | — |
| 31 | 70 / 71 | `00` / `v` | Get/SetWearHabit — pulso esquerdo/direito | 🟡 | — |
| 90 | 70 | `00` | GetFeatureSwitches — máscara de 30 chaves | 🟢 | — |
| 90 | 70 / 71 | `tipo` / `01 tipo on` | Get/SetFeatureSwitch — 14 levantar o pulso, 29 SpO2 contínuo, 21 estresse (FatigueMonitor), 24 tela sempre ligada… | 🟡 | usa (14, 21, 29) |
| 90 | 71 | `00 máscara(4)` | SetFeatureSwitches — todas de uma vez | 🟡 | — |
| 05 | 70 / 71 | `00` / 8 bytes | Get/SetFaceScreen — estilo de data/hora/bateria do mostrador | 🟡 | — |

Os tipos de `FeatureSwitch` são índices de bit: 0 AntiLost, 1 AutoSync, 2 Sleep, 3 AutoSleep, 4 CallsRemind, 14 RaiseHandToActive,
16 RealtimeHR, 17 SuperAlarm, 18 HRMonitor, 21 FatigueMonitor, 24 ScreenAlwaysOn, 25 SportMode, 26 StressMonitor, 28 OffScreenDisplay, 29 Spo2Monitor.

## 3. Saúde: configuração

| cmd | sub | Payload | Comando | Risco | App |
|---|---|---|---|---|---|
| 5C | 70 / 71 | `00` / `min` | Get/SetHeartrateMonitorInterval ✅ (app: 10 = auto, 0 = desligado; 3 testado) | 🟡 | usa |
| 5D | 70 / 71 | `00` / `máx mín on` | Get/SetHeartrateAlarm — UI: alto 100–150, baixo 40–50 | 🟡 | usa |
| 63 | 70 / 71 | `01` / `01 01 v` | Get/SetSpO2MonitorInterval (= SetSpO2Alert no envio) | 🟡 | usa (alerta) |
| 63 | 70 / 71 | `02` / `02 01 v` | Get/SetStressMonitorInterval | 🟡 | — |
| 63 | 70 / 71 | `0D` / `0D 01 v` | Get/SetPlateauCare — cuidado de altitude (5 = ligado, 0 = desligado) | 🟡 | usa |
| 30 | 70 / 71 | `00` / `gênero idade altura peso×10(2) ano(2) mês dia` | Get/SetUserInfo — perfil (0 M, 1 F) | 🟡 | usa |
| 50 | 70 / 71 | `00` / `tipo valor(2) 01` | Get/SetGoal — 0 passos/100, 1 kcal, 4 min ativos, 5 distância m/100 | 🟡 | usa |
| 5E | 70 / 71 | `00` / 13 bytes | Get/SetSedentaryReminderForLS16 — padrão 09–21 h, 60 min, pausa 12–14 h | 🟡 | usa |
| 32 | 71 | TLVs 1–8 | SetPhysiologicalPeriodSetting — ciclo menstrual e lembretes | 🟡 | usa |
| 33 | 71 | varints | SetPhysiologicalPeriods — histórico de períodos | 🟡 | — |
| 1A | 71 | `27` / `28` | ControlSpO2 — inicia/para medição de SpO2 | 🟡 | — |

## 4. Saúde: histórico (`0xBE`)

Pedido: contagem `00 <tipo> 08 <início 4B> <fim 4B>`; dados `<tipo> <tipo> 08 <início> <fim>` (epoch em segundos, LE).
Resposta: N pacotes com pares varint (campo, valor); campos 0, 1 e 2 abrem um novo ponto (tempo absoluto, +s, −s).

| Tipo | Dado | Campos | App |
|---|---|---|---|
| 1 | passos ✅ | 3 passos, 4 cal (/1000 = kcal), 5 metros, 6 duração | usa |
| 2 | atividade | 12 estado | usa |
| 3 | sono ✅ | 13 estado (0 profundo, 1 leve, 2 acordado, 5 REM, 16 entrou, 17 saiu) | usa |
| 4 | batimentos ✅ | 7 bpm | usa |
| 5 | estresse | 10 nível | usa |
| 6 | SpO2 | 11 % | usa |
| 7 | treinos | 16 início, 17 fim, 18 tipo, 19–34 métricas, 82/83/87 pressão | usa |
| 8 | pontos de treino + GPS | como `GetWorkoutPoints` (0xBC): 3 passos … 10–15 GPS | usa |
| 9 / 0A / 0B | pressão arterial / PAI / VO2max (sem intervalo: payload só `09`, `0A`, `0B`) | 14/15 sistólica/diastólica; 48–54 PAI; 55 VO2max | usa (VO2max) |

Também GET: `5B 70 00 00` histórico de FC (legado), `5F` HRV, `54` esportes, `56` sono, `66`/`67` treinos, `6E 70 00` treino ao vivo,
`64 70 03 00 00 00` estado corporal/recuperação, `52 70 00` contagens, `62 70 01` **último batimento**.

### Apagar histórico 🟠 (o app nunca usa)

| cmd | Payload | Apaga |
|---|---|---|
| 5A | `00` / `01` / `02` / `03` | FC / HRV / estresse / "blood" (provável SpO2) |
| 53 | `00` | esportes |
| 55 | `00` | pontos de sono |
| 68 | `00` | treinos |
| BE | `09` / `0A` / `0B` | pressão / PAI / VO2max |

## 5. Notificações, chamadas e mensagens

| cmd | sub | Payload | Comando | Risco | App |
|---|---|---|---|---|---|
| 79 | 71 | ver PROTOCOLO.md | PushMessage — notificação; tipo 5 = chamada recebida (com TLV 5 = telefone), 6 = encerrada ✅ | 🟡 | usa |
| 9E | 70 | `00` / `01` | GetSocialAppSwitches / GetSocialApps | 🟢 | — |
| 9E | 71 | `00 01 02 tipo on` | SetSocialAppSwitch — o app só liga "Outro" (49); o filtro por app é feito no celular | 🟡 | usa |
| 9E | 71 | `00 00 + 32 bytes` | SetSocialAppSwitches — bitmap de 256 apps | 🟡 | — |
| DA / DB | 70 | `00` | contagem / lista de respostas rápidas | 🟢 | usa |
| DB | 71 | `03 00 01 qtd …` | SetQuickReplies (máx. 3 na UI); vazio = `03 03 00` apaga tudo | 🟡/🟠 | usa |
| DB | 71 | `01 …` / `03 02 01 id` | EditQuickReply / DelQuickReply | 🟡/🟠 | — |
| 91 | 71 | `0C on` | SetIncomingReplySwitch — resposta rápida em chamada | 🟡 | usa |
| 06 | 70 | `00` / `01` | GetContactsNum / GetContacts | 🟢 | — (LS23 usa cache) |
| 06 | 71 | `00 01 N [01 01 idx contato]…` | SetContacts — até 100 no LS23; vazio apaga a agenda | 🟡/🟠 | usa |
| 0F | 71 | TLV 3 {1 fone, 2 nome} | SetEmergencyContact — SOS | 🟡 | usa |
| 12 | 71 | `04 + TLVs` / `02 00` | SetDeviceBullets — mensagens prontas; `02 00` limpa | 🟡/🟠 | — |
| 75 | 71 | início/sync/fim | SyncPhoneSchedules — agenda do celular (máx. 30) | 🟡 | — |

## 6. Alarmes e lembretes (`0x9C`, `0x9D`, `0x9B`)

| cmd | sub | Payload | Comando | Risco | App |
|---|---|---|---|---|---|
| 9C | 70 | `00` / `03` / `02` / `07 tipo` | contagens / alarmes / lembretes / eventos por tipo | 🟢 | usa |
| 9C | 71 | `06 (id|n<<4) TLVs` | SetAlarms — máx. 8; TLV 2 = dias (seg=1 … dom=64) | 🟡 | usa |
| 9C | 71 | `06 …` TLV 0 = tipo | SetReminderEvents — remédio (1), água (2)…, máx. 8 cada | 🟡 | usa |
| 9C | 71 | `05` / `04` / `03` / `02 id` / `07 tipo` | apagar alarmes / lembretes / ambos / um / por tipo | 🟠 | usa (lista vazia) |
| 9D | 70 / 71 | `00` / `00 on TLVs` | lembrete de beber água (horário, intervalo) | 🟡 | — |
| 9D | 70 / 71 | `01` / `01 on TLVs` | lembrete de lavar as mãos | 🟡 | — |
| 9B | 70 / 71 | `00 00` / `op 00 …` | registros de água (0 add, 1 edit, 2 apaga, 3 apaga tudo, 4 substitui) | 🟡/🟠 | — |
| 40 | 70 / 71 | `00` / `00 …` / `03 …` / `01 id` / `02` | relógio mundial: listar / adicionar / editar / apagar uma / apagar todas | 🟡/🟠 | usa |

## 7. Mostradores, cartões e arquivos

| cmd | sub | Payload | Comando | Risco | App |
|---|---|---|---|---|---|
| 0F | 70 | `02` | GetWatchfaceID | 🟢 | usa |
| 11 | 70 | `01` / `00` | nome do mostrador atual (`WF<id>`) / lista de nomes ✅ | 🟢 | usa |
| 1E | 70 | `00` | GetWatchfaceIDs | 🟢 | — |
| 42 | 71 | `01 id(4)` | SwitchWatchface — troca o mostrador ativo | 🟡 | — |
| 0F | 71 | TLV(2,4,id) | SetWatchfaceID | 🟡 | — |
| 11 | 71 | `01 00 len nome` | DeleteWatchfaceByName | 🟠 | — |
| 42 | 71 | `02 id(4)` | DeleteWatchface | 🟠 | — |
| 1E | 71 | `02 id …` / `03 id …` | SetCustomWatchface / SetNewCustomWatchface (widgets) | 🟡 | — |
| — | — | zip via SiFli | instalar mostrador (loja ou DIY, 466×466 no app) — **caminho real do LS23** | 🟡 | usa |
| 42 | 70 / 71 | `00` / `01 id` · `00 …` add, `01 id` apaga, `02` apaga todos, `03` edita, `04` ordena | cartões eletrônicos (QR/código) | 🟡/🟠 | usa |
| 63 | 71 | `05 00` / `06 …` | apagar todos / alguns arquivos do álbum | 🟠 | — |
| 41 | 70 | `07` / `08` | dados/IDs do álbum | 🟢 | — |

## 8. Música, câmera, localizar

| cmd | sub | Payload | Comando | Risco | App |
|---|---|---|---|---|---|
| D0 | 80 | `03 TLVs` | SetDeviceMusicInfo — título, tocando/pausado, volume, posição | 🟡 | usa |
| D0 | 80 | `02 vol` / `00|01 nome` | SetMusicVolumn / SetMusicState | 🟡 | usa |
| 1A | 71 | `22` | ControlDevice FindDevice — faz o relógio tocar | 🟡 | usa |
| 1A | 71 | `10` | PhoneFound — encerra o "procurar celular" | 🟡 | — |
| 1A | 71 | `0B` / `0C` | entrar / sair do modo câmera remota | 🟡 | usa (sair) |

## 9. Clima e localização

| cmd | sub | Payload | Comando | Risco | App |
|---|---|---|---|---|---|
| 78 | 71 | TLVs (2 cidade, 3 tipo, 4 temp, 8/9 mín/máx, 29 previsão…) | SetWeather | 🟡 | usa |
| 63 | 71 | TLV 7 {1 chip, 2 chave} | SetGpsInfo — o app manda sempre `CC1165W` + chave fixa | 🟡 | usa |
| 63 | 71 | TLV 4 (lat/lon ×1e6, epoch) | SetCurrentGpsLocation | 🟡 | usa |
| 63 | 71 | TLV 15 / 11 / 12 / 10 | posição aproximada / centro / área / tema do mapa | 🟡 | — |
| 03 | 71 | `13 uuid auth` | SetDeviceMapAuthCode | 🟡 | — |

## 10. Segurança, vínculo e manutenção 🔴

| cmd | sub | Payload | Comando | Risco | App |
|---|---|---|---|---|---|
| 03 | 70 | `10` / `12` | GetDevicePasscode / …AndSwitch — **lê a senha em texto puro** | 🟢 (sensível) | usa |
| 03 | 71 | `12 on senha` / `10 senha` / `11` | define / remove senha de bloqueio (4 dígitos) | 🟡 | usa |
| 93 | 71 | `00 + md5_16(userId)` | StartConfirmBind — troca o dono; o relógio pede confirmação na tela | 🔴 | usa (toda conexão com UID diferente) |
| 93 | 71 | `01` / `02` | StartBind / StartQRCodeBind | 🔴 | — |
| 92 | 71 | TLVs | StartSifliBindWithoutConfirm — vínculo sem confirmação | 🔴 | — |
| 94 | 71 | `01` | EndBind — fecha o pareamento | 🔴 | usa |
| 91 | 71 | `01 0|1` | SetBTSwitch — liga/desliga o BT clássico (chamadas) | 🟡 | usa |
| 1A | 71 | `25 auto` | TurnOnBTSwitchWithOption | 🟡 | usa |
| 1A | 71 | `24` | DisconnectBt | 🔴 | — |
| 1A | 71 | `11` / `26` | reiniciar (ControlDevice Reboot / RebootDevice) | 🔴 | — |
| 1A | 71 | `01` | desligar | 🔴 | — |
| 1A | 71 | `23` | ClearData — apaga os dados do relógio | 🔴 | — |
| 0D | 71 | `00` | **ResetDevice — reset de fábrica e desvínculo** | 🔴 | usa (restaurar, desvincular, **logout**) |
| 0E | 71 | `00 endereço(4)` | EnterOta — modo de atualização (Huawo) | 🔴 | — (LS23 usa SiFli DFU) |
| 1E | 70 | `01 id(4)` | GetOtaAddress | 🟢 | — |

## 11. Bits de `GetDeviceFeatures` (`6F 41 70 01 00 02`)

Resposta tratada como dois inteiros LE: **A** = bytes 0–3, **B** = bytes 4–7.

- **A:** 0 GPS, 1 NFC, 2 atualização de AGPS, 3 esportes, 9 WeChat, 10 AliPay, 12 alerta de SpO2, 13 menu configurável, 14 idioma,
  17 temperatura, 20 pacote de idioma, 28 lembrete de remédio, 29 de água, 30 relógio mundial, 31 tarefas.
- **B:** 10 achar relógio, 11 bloqueio ao tirar do pulso, 12 cartões, 13 SOS, 14 resposta rápida, 15 música, 16 álbum,
  17 mostrador DIY dinâmico, 18 voz de exercício, 19 SpO2 o dia todo, 20 VO2max, 21 mostrador por IA.
- O app força no LS23: GPS, AGPS, VO2max e "levantar o pulso"; no `LS23002` também mostrador por IA e cuidado de altitude.

## 12. Fora do escopo do cliente local

- **IA (`0xD6`, `0xBF`)**: ~20 classes de respostas de IA, reunião, tradução e gravação de voz. Dependem da nuvem do fabricante.
- **Oração islâmica (`0xE7`)**: horários, contas de oração, bússola.
- **Treino controlado pelo celular (`0x6E`)**: iniciar/pausar treino e mandar dados ao vivo (valores de "ação" não resolvidos).

## Leituras confirmadas no relógio (2026-10-23, `bridge/solaris_probe.py`)

Sonda com 63 GET (só `sub 0x70`): **62 responderam**; `GetBodyState` (`64 70 03 00 00 00`) não responde no LS23.

- **Com dado:** DeviceID, DeviceType, FirmwareVersion, PasscodeAndSwitch, FaceScreen, Brightness, BatteryAndState, Language,
  SupportedLanguages (29 idiomas), Unit, TimeFormat, WatchfaceName, WatchfaceNameList, UpgradeStatus, BTConnectionState,
  WeatherUnit, DisplayDuration, UserInfo, WorldClockCityIDs, WorkoutState, SleepState, DeviceFeatures, armazenamento de música/álbum,
  GpsStatus (validade do AGPS), ProtocolVersion, WatchfaceVersion, SN, VersionForLS, Goal, ActivityNum, HeartrateMonitorInterval,
  HeartrateAlarm, SedentaryReminderForLS16, **NewestHeartrate (0x62)**, SpO2MonitorInterval, StressMonitorInterval, SpO2Alert,
  WorkoutCount, FeatureSwitches, UID, BindState, ReminderAndAlarmNum, **Alarms (um pacote por alarme)**, DrinkWater/WashHand,
  SocialAppSwitches, QuickReplyCount; histórico 0xBE de **estresse**, **SpO2** e **treinos** (atividade veio vazia).
- **Responde vazio** (comando existe, sem dado): ContactsNum, Volumn, WatchfaceID, WatchfaceIDs, WearHabit, PlateauCare,
  MapAvailableStorage, SocialApps, AllElectronicCard, Reminders, QuickReplies, AllDrinkWaterRecords.

Diferenças em relação ao que o código do app espera:

- `GetDeviceFeatures` vem como TLV `02 10 <16 bytes>`; os inteiros A e B da seção 11 são os 8 primeiros bytes do dado.
- `GetSpO2Alert` responde com TLV tipo 3 (o parse do app procura o tipo 1).
- `GetUserInfo` tem 5 bytes (gênero, idade, altura, peso×10) — sem data de nascimento.
- `GetSedentaryReminderForLS16` tem 16 bytes; os 8 finais parecem lixo de memória (texto ASCII).
- `GetMusicAvailableStorage` só devolve o TLV 3 (livre), não o 13 (total).
- O nome do mostrador vem no formato `JW_LS20WF0190`, não `WF<id>`.
- `GetSocialAppSwitches`: as 256 chaves estão ligadas.

## Próximos passos sugeridos

1. ~~Corrigir `GetNewestHeartrate` para `0x62`~~ — feito e confirmado.
2. Levar as leituras novas (SpO2, estresse, alarmes, configurações) para o painel.
3. Testar ajustes 🟡 lendo o valor antes e gravando de volta (brilho, tempo de tela, levantar o pulso, alarmes).
4. Deixar 🟠 e 🔴 de fora do painel, ou só atrás de confirmação explícita.
