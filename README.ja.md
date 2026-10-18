[English](README.md) | [中文](README.zh-CN.md) | 日本語

# Groki Bot

Groki Bot デスクトップロボットのファームウェア、ゲートウェイ、Grok Bot 連携をまとめたリポジトリです。M5Stack の CoreS3 / AtomS3R / AtomS3 / StopWatch (C152) で動く
Stack-chan ファームウェア。ESP-IDF 5.5 / C++20。AI 音声対話 (OpenAI / Gemini / XiaoZhi)、
BLE / Wi-Fi / SoftAP の 3 経路設定、デバイス側 OTA をサポートします。

Groki は Grok bot バックエンドによるネイティブ音声に対応し、デスクトップアプリと連携してロボットと直接会話できるようになりました。Grok Bot にログイン済みの Mac であれば既存のログイン状態で音声を利用でき、追加の xAI 音声 API キー設定やファームウェアの再書き込みは不要です。

> 目の表現には [aora-bot](https://github.com/sam70361/aora-bot) Emotion Ball の
> アイリング輪郭を使っています。このデータは Emotion Ball Community License に基づく
> **非商用利用のみ** の許諾です（全文は [third_party/emotion-ball/](third_party/emotion-ball/)）。
> PC 側のゲートウェイ（Gemini / Grok ログイン音声、ウェイクワード、MCP、ネイティブ Grok Bot 連携）は [gateway/](gateway/README.md)、
> 全体の構成と通信は [docs/architecture.md](docs/architecture.md)（英語）を参照してください。
> ウェイクワードは既定で「Hi Grok」、`gateway/.env` に `STACKCHAN_WAKE_PHRASE=hey groki` を設定すると「Hey Groki」になります。

## クイックスタート：Grok Bot で会話する（macOS のみ）

必要なもの：Groki Bot 本体（CoreS3 + Stack-chan ベース）、USB-C ケーブル、デスクトップ版 Chrome または Edge、同じ Wi-Fi 上で Grok Bot アプリにログイン済みの Mac（動作確認バージョン 0.71.2、内部 API を使用しているためアプリのアップデートにより動作しなくなる可能性があります）、Node.js、[uv](https://docs.astral.sh/uv/)。

1. ファームウェアの書き込みと Wi-Fi 設定：[path 1](README.md#path-1-firmware-only) のステップ 1 と 2 を行います。API キーの入力はスキップできます。
2. Mac にゲートウェイをインストール：本リポジトリを `git clone` し、`cd groki-bot/gateway`、`uv sync --all-extras`、`cp .env.example .env` を実行します。
3. [Grok Bot ネイティブ音声の設定](#grok-bot-ネイティブ音声の設定) に従って、`gateway/.env` の 4 項目を設定します。
4. 別のターミナルで [Grok Bot ネイティブ音声の設定](#grok-bot-ネイティブ音声の設定) の完全なコマンドを実行し、ログインヘルパーを常駐させます。
5. [path 2](README.md#path-2-firmware--gateway) と同様にゲートウェイを起動し、BLE 設定ページでロボットの接続先を `ws://<コンピュータの LAN IP>:8765` に設定して、「Hi Grok」と話しかけます。

デフォルトのバックエンドは Gemini のままです。`STACKCHAN_VOICE_BACKEND=gemini` を設定することでいつでも戻せます。その他の音声オプション（OpenAI Realtime、Gemini Live、`gbot` CLI によるタスク転送）は後述の [他の音声バックエンド](#他の音声バックエンド) を参照してください。

### Grok Bot ネイティブ音声の設定

コードの既定の音声バックエンドは Gemini のままで Grok には自動で切り替わらないため、Grok Bot ネイティブ音声を使うには `gateway/.env` で自分で切り替えてください。

ネイティブ音声は macOS のみで、Grok Bot デスクトップアプリの内部 API（検証バージョン 0.71.2）を使うためアプリ更新で動作しなくなる可能性があり、動作しない場合や利用をやめる場合は Gemini の API キーを残したうえで `STACKCHAN_VOICE_BACKEND=gemini` に変更してゲートウェイを再起動すれば、ファームウェアの再書き込みは不要です。

`gateway/.env` に次の 4 行を追加し、名前と ID を自分のものに置き換えてください。

```dotenv
STACKCHAN_VOICE_BACKEND=gbot
STACKCHAN_GBOT_VOICE_BOT_ID=YOUR_AGENT_ID
STACKCHAN_TOOL_BOT=assistant
STACKCHAN_TOOL_BOT_ID=YOUR_AGENT_ID
```

- `STACKCHAN_VOICE_BACKEND=gbot` は Grok 音声バックエンドを選択します。
- `STACKCHAN_GBOT_VOICE_BOT_ID` は音声セッションで使うエージェントの ID です。
- `STACKCHAN_TOOL_BOT` は自分で付けたエージェント名で、コードは空でないことだけを確認し、アプリ内で名前を使った検索は行いません。
- `STACKCHAN_TOOL_BOT_ID` はデスクトップタスクで使うエージェントの ID で、音声用の ID と一文字ずつ一致させる必要があり、大文字、小文字、空白は自動で正規化されません。

自分のエージェント名と、その同じエージェントの ID を使ってください。任意の `gbot` CLI がある場合は、次のコマンドで確認できます。

```sh
gbot bots list
gbot bots list --json
gbot bots get "<名前または ID>"
```

これらのコマンドは、この Mac 上の Grok Bot エージェントディレクトリを読みます。ディレクトリが見つからない場合はエラーとなり、`--dir` または環境変数 `GROK_BOT_AGENTS_DIR` で場所を指定するよう表示されます（2026-10-18、gbot 0.11.3 で確認）。`--json` は JSON 出力を指定しますが、ヘルプには出力の列名やフィールド名は記載されていません。ID を取得できない場合は先に進まず、Grok Bot アプリまたは自分のエージェント設定で ID を確認してください。ID があれば、ネイティブ音声の実行自体に CLI は不要です。

同じ Mac の Grok Bot デスクトップアプリにログインしてください。別のターミナルを開き、`gateway/` で同じ名前と ID を渡して次の完全なコマンドを実行し、そのターミナルを開いたままにしてください。

```sh
STACKCHAN_GBOT_VOICE_BOT_ID=YOUR_AGENT_ID \
STACKCHAN_TOOL_BOT=assistant \
STACKCHAN_TOOL_BOT_ID=YOUR_AGENT_ID \
node scripts/gbot_voice_session_bridge.mjs
```

ログインヘルパーは `.env` を読み込まないため、この 3 つの環境変数を明示的に渡します。待ち受け先は `127.0.0.1:18771` のみです。

任意設定：`STACKCHAN_GBOT_VOICE_LANGUAGE` の既定値は `zh` で、必要に応じて `gateway/.env` で `en` または `auto` に変更できます。その他の詳細設定は [gateway setup](gateway/README.md#grok-bot-login-voice-macos) を参照してください。

ゲートウェイ起動ログに `voice_backend=gbot` があることを確認し、別のターミナルからログインヘルパーも確認してください。

```sh
curl http://127.0.0.1:18771/health
```

ヘルパーが動いていれば `"ok": true` が返ります。この 2 つはバックエンドの選択とヘルパーの設定を確認するもので、ログインの有効性や音声接続の成功を保証しません。ロボットに「Hi Grok」と話しかけ、応答が聞こえることを確認してください。

Gemini に戻すには、`STACKCHAN_VOICE_BACKEND=gemini` に変更するかその行を削除し、有効な `GEMINI_API_KEY` を残してゲートウェイを再起動してください。別の起動コマンドやサービスの環境でも `gbot` を指定している場合は、そちらも変更してください。独立したログインヘルパーは自動終了しないため、不要になったらそのターミナルで終了してください。

Grok 音声の起動が失敗してもゲートウェイは Gemini に自動で戻らず、ロボットがオンライン表示でも音声が出ない場合があり、ログには `Grok voice startup failed` が出るため、その場合は上記の手順で Gemini に戻してください。


### Grok Bot のログイン状態で音声対話

macOS でログイン済みの Grok Bot アプリがあれば、ゲートウェイの `STACKCHAN_VOICE_BACKEND=gbot` を指定して音声対話を利用できます。別途 xAI 音声 API キーを設定する必要はありません。デフォルトは Gemini のままで、`STACKCHAN_VOICE_BACKEND=gemini` に戻せます。ゲートウェイの切り替えにファームウェアの再書き込みは不要です。

Grok 側はネイティブの `send_task`、`search_conversations`、`end_the_call` と共通のローカル機器ツールを直接使用します。`gbot` CLI は不要で、CLI 転送サービスは Gemini 側のオプション連携機能として残されています。[セットアップ手順](gateway/README.md#grok-bot-login-voice-macos)（英語）を参照してください。

実機検証において、タスクの受付、完了結果の音声モデルへの伝達、およびモデルによる出力音声の生成は確認済みです。実機スピーカーでの具体的な発話内容も正常であることを確認しました（2026-10-18）。デスクトップのテキストメッセージ購読は一時的にエラーとなる場合があり、その際は個別に再接続されます（切断中に届かなかったメッセージの再送は行われません）。CoreS3 ファームウェアは発話中にマイク入力を停止する場合があるため、声だけの割り込みが動作するとは保証できません。詳細は[検証範囲](gateway/GBOT_VOICE_VALIDATION.md)を参照してください。

## 他の音声バックエンド

OpenAI Realtime、Gemini Live、`gbot` CLI によるタスク転送は [英文 README](README.md#other-voice-backends) を参照してください。

## Web Flasher / 設定ページ

GitHub Releases に置かれたファームウェアをブラウザから書き込めます (Chrome / Edge):

- **書き込み**: <https://josssphzhou.github.io/groki-bot/>
- **BLE 設定**: <https://josssphzhou.github.io/groki-bot/settings.html> (Web Bluetooth、デスクトップ Chrome / Edge のみ)
- **Wi-Fi 設定**: デバイスを Wi-Fi に繋いだ後 `http://stackchan-XXXXXX.local/` (mDNS)
- **iOS / SoftAP 設定**: 本体ボタン (boards により方法が異なる、後述) で AP モードに入り、
  LCD に表示される Wi-Fi QR を iPhone Camera で読む → captive portal で設定ページが自動表示

タグ `vX.Y.Z` を push すると CI が 4 ボード分ビルドして Release を作り、
Pages サイトに反映されます。

## 対応ボード

| ボード | 略号 | 表示 | 主な特徴 |
|---|---|---|---|
| CoreS3 + Stack-chan ベース | `cores3` | 320×240 IPS + touch | 標準。サーボ 2 軸、頭タッチ センサー、INA226 電池計 |
| CoreS3 + Takao Base | `cores3` | 同上 | Port A 半二重サーボ、サーボ電源/電池計なし |
| AtomS3R + Atomic ECHO BASE (アトムニャン) | `atoms3r` | 128×128 LCD | サーボなし、ES8311 音声、BtnA で UI / AP |
| AtomS3 (PSRAM なし) + ECHO BASE | `atoms3` | 128×128 LCD | 軽量プロファイル、会話/RTP 非対応 |
| M5 StopWatch (C152) | `stopwatch` | 466×466 円形 AMOLED + touch | サーボなし、touch で視線追従、ES8311 音声 |

ビルドは `make build BOARD=<略号>` (デフォルト `cores3`)。
ボードは起動時に検出され、`set_board_kind()` で UI / 機能のグレーアウトに反映されます。

## 機能

- **AI 音声対話**: OpenAI Realtime / Google Gemini Live / XiaoZhi サーバーに WebSocket
  接続。マイク入力 → 応答音声 → 口パク連動。半二重 CoreS3 では発話中マイクを止め、
  応答中は LCD タップ / 頭タッチ (Si12T) で barge-in 可能。サーバー側 VAD で
  ターン検出。OpenAI / Gemini ではツール (`set_expression` / `set_head_pose` /
  `speak_katakoto`) で表情・首振り・カタコト声を制御、XiaoZhi は感情を表情にマップ。
  応答テキストは吹き出しに進行表示。
- **アバター描画**: M5GFX で 30 fps。呼吸 / saccade / blink、6 表情 (Neutral / Happy
  / Sad / Angry / Doubt / Sleepy)。**Avatar DSL** (`.avdsl` ソース → `.avbc` バイトコード)
  で顔のレイアウトとアニメーションを差し替え可能 (BLE / Wi-Fi 経由でライブ更新)。
- **マイク連動口パク (lip sync)**: FFT + 帯域 log + spectral flux で開口度推定、
  雑音床 EWMA AGC で環境ノイズに追従。
- **サーボ**: SCS0009 yaw + pitch を UART1 (1 Mbps) で制御。台形速度
  プロファイル `PathGenerator`、駆動時のみトルク有効化。ボード別レンジ
  キャリブレーション (ServoLimits) を NVS 保存。
- **顔追従** (Mac 上のゲートウェイと併用): Mac のカメラで顔を検出する
  [tools/vision-tracker/](tools/vision-tracker/README.md) の位置情報から、ゲートウェイが
  XiaoZhi の `head` メッセージで首を顔の方へ向けます。ロボットが話している間と
  ウェイクワード後に聞いている間は追従を止めます。設定手順は
  [gateway/README.md の Face tracking](gateway/README.md#face-tracking)（英語）。
- **スピーカー / オーディオ**: 起動音 (C5–E5–G5、設定で OFF 可)、jtts ランダム
  babble、AAC 録音再生、BLE オーディオ ストリーム、Wi-Fi RTP (L16 / μ-law / AAC) 受信。
  音量は **0..200%** をライブ制御 (BLE / Wi-Fi / 本体 UI)。
- **NeoPixel**: nekomimi 用 LED ストリップ アニメーション (虹 / 単色 / リップシンク
  レベルメーター モード)。
- **LT タイマー**: 発表時間アシスト。残り N 秒で予告 → 時刻ぴったり → 超過繰返し
  アナウンス (jtts)。
- **オンデバイス UI**:
  - CoreS3 / StopWatch (touch panel): 画面右上タップで 5 タブ UI (情報 / 設定 /
    操作 / 範囲 / 会話 / LT)
  - AtomS3R / AtomS3 (button only): BtnA 短押でステータス オーバーレイ、長押で
    operation_mode サイクル
- **吹き出し**: 24 px ゴシック フォントの白パネル、長文は marquee スクロール。
- **BLE 設定サービス** (NimBLE GATT): `tools/settings.html` (Web Bluetooth) から
  Wi-Fi 接続先・API キー・各種設定・OTA 更新。Bluetooth 4.2 以降の Just Works
  ペアリング + アプリケーション層 X25519 + AES-256-GCM 暗号化、optional password 認証。
  設定は NVS に保存、Apply で再起動して反映。
- **Wi-Fi 設定サービス**: 接続後 HTTP サーバー (port 80) + `settings_wifi.html`、
  mDNS (`stackchan-XXXXXX.local`)。SSID / プロバイダ / API キー / システム
  プロンプト / 追加 HTTP ヘッダ / jtts / Avatar DSL / OTA 全て対応。
- **SoftAP プロビジョニング (iOS フレンドリー)**: STA が未設定 / 接続失敗時に
  本体トリガで AP モード起動 (`Stackchan-XXXXXX` + WPA2)。LCD に Wi-Fi QR 表示、
  iPhone Camera スキャン → 接続 → **captive portal が自動で `settings_wifi.html` を開く**
  (DNS hijack + HTTP 404 catch-all)。AP モード中は require_auth が bypass され、
  設定が即触れる。
- **OTA 更新**: デュアル OTA パーティション、起動検証 + ロールバック対応。
  - **BLE 経由**: settings.html → 暗号化された chunk
  - **Wi-Fi ローカル ファイル**: settings_wifi.html で `.bin` をアップロード
  - **Wi-Fi デバイス側 fetch** (v0.7.4+): `POST /api/ota/release {tag}` で device
    自身が GitHub Pages から自分のボード用バイナリをダウンロード → 適用 (STA 必要)

## ハードウェア (CoreS3 + Stack-chan ベースの場合)

- M5Stack CoreS3 (ESP32-S3、8 MB Quad-SPI PSRAM、16 MB Flash)
- Stack-chan ベース (PY32 IO Expander @ 0x6F、SCS0009 ×2)
  - 内部 I²C: AXP2101 (0x34) / Touch (0x38) ほか M5Unified が管理
  - PY32 Pin 0: サーボ VM 電源 EN (ON 後 200 ms 待ってバス使用)
  - サーボ バス (SCS0009): UART1, TX GPIO 6 / RX GPIO 7, 1 Mbps, 8 N 1
    - Yaw  ID = 1, zero_pos = 460
    - Pitch ID = 2, zero_pos = 620
  - 1 step ≈ 0.3125° (`deg = (raw - zero) * 5 / 16`)
  - 頭タッチ センサー Si12T @ 0x68 (なでなで / barge-in、3 ゾーン)
- 他ボードのピン配置は各 `sdkconfig.defaults.<board>` と
  `components/board/board.cpp` を参照

## クイックスタート

Docker でのビルドを推奨します。ホストに ESP-IDF を入れる必要はありません。
先に Docker Desktop（または Docker デーモン）を起動してください。

### Docker（推奨）

イメージは `espressif/idf:release-v5.5` です。初回の pull は約 14 GB です。
公式イメージに Node は入っていません。Makefile がコンテナ内で Node.js 18+
を自動インストールします。成果物は `build-cores3/` です。

```sh
git clone https://github.com/JosssphZhou/groki-bot.git
cd groki-bot
git submodule update --init --recursive
tools/apply-m5-patches.sh                    # M5Unified の 1 行修正を適用
make build-docker BOARD=cores3
```

`BOARD=` を `stopwatch` に置き換えられます。本版でビルドを検証済みなのは
`cores3` と `stopwatch` です。`atoms3r` / `atoms3` はまだ再ビルドしていません。以前の失敗原因
(顔アニメ資源が 1 MB の storage パーティションに載らない) は資源の削除で解消済みです。詳細は
[既知の問題 第 4 条](docs/known_issues.md)。Docker 経路でも同じ `BOARD` を
渡す必要があり、対応する sdkconfig デフォルト列が読み込まれ、成果物は
`build-<board>/` に置かれます。

フラッシュは Docker では行いません。いまビルドしたファームを書くには:

```sh
make flash BOARD=cores3 PORT=/dev/ttyACM0
make monitor BOARD=cores3 PORT=/dev/ttyACM0
```

`make flash` にはホスト側の ESP-IDF が必要です（IDF 環境を読み込んでから
`idf.py flash`）。ホストに IDF が無い場合は、公開済みリリースをブラウザ
書き込みページで焼いてください:
<https://josssphzhou.github.io/groki-bot/>。
このページは GitHub Release を読むだけで、`build-cores3/` のファイルを
直接選べません。

### ホスト ESP-IDF

環境: ESP-IDF 5.5（5.5.4 で検証）。Espressif の手順で入れたあと、IDF
ソースディレクトリで `./install.sh esp32s3` を実行し、`source export.sh`
してください。Makefile の既定は `IDF_PATH=$(HOME)/esp-idf/5.5.4` です。
ビルド機の PATH に Node.js 18+ が必要です（CMake 設定時に `node` を探し、
`.avdsl` をバイトコードへコンパイルします。コンパイラは
`tools/avatar_dsl/` にあります）。システムの `python3` が 3.14 だと、
IDF 5.5 は対応する仮想環境を探し、未導入なら make はすぐ失敗します。

```sh
git clone https://github.com/JosssphZhou/groki-bot.git
cd groki-bot
git submodule update --init --recursive
tools/apply-m5-patches.sh                    # M5Unified の 1 行修正を適用
make set-target BOARD=cores3                 # 初回のみ (BOARD 別に build dir が分かれる)
make build     BOARD=cores3
make flash     BOARD=cores3 PORT=/dev/ttyACM0
make monitor   BOARD=cores3 PORT=/dev/ttyACM0
```

ホストの `make build BOARD=<略号>` の成果物は `build-<board>/` です。

CMake が `Could not find NODE_EXECUTABLE` と出した場合、今の環境
（Docker イメージを含む）の PATH に `node` がありません。ソース欠落では
ありません。

`tools/apply-m5-patches.sh` は upstream M5Unified の
`RTC_PowerHub_Class::setAlarmIRQ` で GCC 14 の `-Werror=maybe-uninitialized`
に引っかかる `buf` 初期化を当てるだけです。

OpenAI / Gemini の API キーはビルドに埋め込まず、BLE / Wi-Fi 設定 IF から
実行時に投入します (NVS に保存)。`sdkconfig.defaults.local` (gitignore 済み) で
コンパイル時デフォルトを与えることもできます。

## 起動シーケンス (CoreS3 標準パス)

1. M5 / Avatar 初期化、起動音 (C5–E5–G5 アルペジオ、設定で OFF 可)
2. NVS から設定読み込み → BLE 設定サービス起動 (常時 advertising)
3. SSID があれば Wi-Fi STA 接続を非ブロッキング開始
   - STA 接続後 → mDNS + HTTP 設定サーバー + SNTP 開始
4. マイク loopback (2 秒録音 → 再生) で動作確認
5. サーボ電源 ON → ping (Yaw / Pitch) → 1.5 s 待機
6. Render Task (顔描画 30 fps, core 1) + Servo Task (20 ms 周期, core 0) を起動。
   会話が有効なら Conversation Task が Wi-Fi 接続を待って AI 対話を開始
7. demo_loop 開始 — 会話アイドル時はランダム babble + ランダム ポーズ +
   なでなで反応、会話中は AI 対話タスクがアバターと音声を制御
8. 画面右上タップでオンデバイス UI、応答中の画面タップで barge-in、
   操作タブの「AP モード」で SoftAP プロビジョニング

## リポジトリ構成

```
.
├── components/
│   ├── avatar/             顔描画 + アニメーション (Breath / Saccade / Blink、6 表情)
│   ├── avatar_vm/          Avatar DSL バイトコード VM + ストレージ
│   ├── board/              CoreS3 / AtomS3R / StopWatch HW 初期化 (ボード自動検出)
│   ├── scs_servo/          SCS0009 ドライバ + PathGenerator (台形速度)
│   ├── jtts/               日本語カタコト TTS (babble / speak_katakoto / LT 通知)
│   ├── conversation/       AI 音声対話クライアント (OpenAI / Gemini / XiaoZhi)
│   ├── config_service/     BLE GATT 設定サービス + NVS + OTA + X25519/AES-GCM
│   ├── wifi_config_service/ Wi-Fi HTTP 設定 + 内蔵 Web ページ + release OTA
│   ├── telegram/           Telegram Bot API (TLS) 通知クライアント (oss)
│   ├── M5GFX/              submodule (upstream)
│   ├── M5Unified/          submodule (upstream + 1 patch)
│   └── tl_expected/        tl::expected backport (submodule)
├── main/                   app_main, render/servo task, demo_loop, ap_screen,
│                           captive_portal, device_ui, atom_status, wifi_sta
├── patches/                upstream-targeted patches
├── tools/                  apply-m5-patches.sh, monitor_log.py, settings.html,
│                           avatar_dsl/ (コンパイラ + WASM 連携),
│                           vision-tracker/ (Mac の顔追従プログラム、Swift)
├── assets/                 .avdsl ソース (default_face, omega_mouth, aokko_face, grok_face)
├── partitions.csv          OTA 配置 (ota_0 / ota_1 / nvs / storage)
├── sdkconfig.defaults*     共通 + ボード別 (.cores3 / .atoms3r / .atoms3 / .stopwatch)
└── Makefile                idf.py の薄いラッパ (BOARD= 切替)
```

## ライセンス

このリポジトリの自前ソース (`components/board`, `components/scs_servo`, `components/avatar`,
`components/avatar_vm`, `components/groki_motion`, `components/jtts`, `components/conversation`,
`components/config_service`, `components/wifi_config_service`,
`components/telegram`, `main`, `tools`) は
**Boost Software License 1.0** ([LICENSE](LICENSE)) の下で配布されます。

Submodule (`components/M5GFX` / `components/M5Unified` / `components/tl_expected/expected`)
と managed_components (`espressif/esp_audio_codec` / `espressif/esp_websocket_client` /
`espressif/mdns` / `espressif/esp_jpeg` / `espressif/esp32-camera` 等)
はそれぞれの upstream ライセンスに従います。

### 第三者ソフトウェア・音声データの帰属表示

HMM 音声合成に使う **hts_engine API** (Modified BSD / 名古屋工業大学・東京工業大学)
と、同梱・配布する **HMM ボイス "Mei"** (CC BY 3.0 / 名古屋工業大学・MMDAgent
Project Team) をはじめとする第三者コンポーネントの帰属表示は
**[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)** にまとめています。
HTML 版 (Web フラッシャー・設定ページからも参照可):
<https://josssphzhou.github.io/groki-bot/licenses.html>。

## 謝辞

Groki Bot は Kenta IDA さんの [stackchan-idf](https://github.com/ciniml/stackchan-idf) (BSL-1.0) から始まりました。Kenta IDA さんと Stack-chan コミュニティに感謝します。
