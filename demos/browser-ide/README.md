# ローカルブラウザで開くcode-serverの境界検証

> [!NOTE]
> Windows版は`.devcontainer/claude/code-server/`の機能になりました。Linux版は、ここの
> bwrap構成を使って、ブラウザではなくホストのVS Codeを隔離する形で
> `.devcontainer/claude/code-server/linux/`に移しました（[手順](../../.devcontainer/claude/code-server/README.md)）。
> このディレクトリは検証の記録として残しています。

code-serverを専用の**ローカルブラウザで直接表示**するデモです。
noVNCや画面転送は使用しません。ソースはcode-serverコンテナと共有し、
編集や統合ターミナルの利用にcloneの受け渡しは必要ありません。

Linux版はブラウザのネットワーク名前空間を分離し、唯一の通信経路を
mitmproxyへのUnix socket中継に限定します。ブラウザのproxy設定を変更しても、
その名前空間に外向きのインターフェースや経路はありません。

**検証範囲:** Windows Home＋NixOS on WSLでは、Windows標準のAppContainerとWFPを使う
ネイティブWebView2ブラウザでIDE操作・通信境界を実測しました。Windows IMEは利用者の
実操作で「普段どおり動く」と確認されています。Sandboxieや追加ドライバは使いません。
Linux版はheadlessで検証済みです。DebianのWayland＋Fcitx5／IBusの実機確認は未完了です。

## 構成

```text
ローカルChromium（通常のウィンドウ、独立したプロファイル）
  ├─ Wayland socket → ローカルcompositor・IME
  └─ 独立したnetwork namespace
       127.0.0.1:18081 → Unix socket → ホスト側の固定宛先中継
         → 127.0.0.1:18081 mitmproxy
             ├─ ide.localhost:8000 → code-server
             ├─ preview.localhost:8001 → 能動的なHTMLプレビュー
             └─ その他 → 451で拒否
```

`receiver.localhost:9000`は外部サービスに見立てた使い捨てコンテナです。
比較試験の許可モードでだけ到達できます。実在の外部サービスや秘密は使いません。

手元のホーム、D-Bus、portal、SSH agent、Docker socketはブラウザへ渡しません。
`xdg-open`と`xdg-email`は拒否用プログラムに置き換えます。絶対パスなどで
それを迂回してプロセスを起動しても、同じmount/network/PID namespace内です。
Wayland socketは表示・入力のための明示的な共有資源です。compositorへの攻撃まで
この試験で安全性を立証したわけではありません。

## 起動

このリポジトリのagentイメージに`/opt/code-server/bin/code-server`があることが必要です。
この環境ではtode同梱のcode-serverを利用していますが、tode自体は起動しません。
別名のイメージなら`AGENT_IMAGE`で指定できます。

```sh
docker compose -f demos/browser-ide/compose.yaml up -d
nix develop .#browser-ide-demo
python3 demos/browser-ide/local-browser.py
```

Waylandセッションがあれば、新しいローカルChromiumウィンドウが開きます。
ブラウザ内のURLは`http://ide.localhost:8000/?folder=/workspace`です。
このURLを普段の無制限なブラウザで開く手順ではありません。

Debianでは`bubblewrap`、`chromium`、`python3`をインストールして同じPythonスクリプトを
実行する経路も用意しています。ただしDebian実機での起動・IMEは未検証です。
X11への自動フォールバックやセッションD-Busの共有はしません。
Fcitx5／IBusはWaylandのtext-input-v3経由を想定しているため、compositorとIMEの
対応状況は[確認用ファイル](workspace/ime.md)で実測する必要があります。

ブラウザの画面から次を試せます。

1. `README.md`を開いてMarkdownプレビューを表示する。
2. プレビュー内のリンクから能動的なHTMLプレビューを開く。
3. フォームPOST、fetch、beacon、別タブ、リダイレクトを試す。
4. コマンドパレットの`Demo: Open Boundary Probe`から、リモート拡張のwebviewを試す。
5. 統合ターミナルで`node hello.js`を実行する。
6. `ime.md`で普段のIME・辞書、候補位置、文節変更、再変換を確認する。

ブラウザ状態は表示される`/tmp/browser-ide-*`に保存されます。
終了はブラウザウィンドウを閉じるか、起動した端末でCtrl+Cです。

## 自動検証

以下は画面を出さない試験です。IMEの確認を代替しません。
`--test`は自動操作用CDPをloopbackへ公開するため、通常利用では指定しません。

```sh
# ターミナル1（上記のnix develop内）
python3 demos/browser-ide/local-browser.py \
  --headless --test --runtime /tmp/browser-ide-check

# ターミナル2（同じnix develop内）
DEMO_RUNTIME=/tmp/browser-ide-check node demos/browser-ide/verify.mjs
```

検証はデモのIDEを再起動します。デモ内の未保存の編集は先に保存してください。
結果とスクリーンショットは`/tmp/browser-ide-evidence/`です。
許可モードと制限モードを比較し、通常終了・試験失敗のどちらでもproxyを制限モードへ戻します。
プロセスを強制終了した場合は、下記のコマンドで制限モードへ戻してください。

```sh
DEMO_CONTROL=0 docker compose -f demos/browser-ide/compose.yaml up -d proxy
docker compose -f demos/browser-ide/compose.yaml logs proxy
docker compose -f demos/browser-ide/compose.yaml exec receiver cat /tmp/received.jsonl
```

強制終了後も許可モードで開くのは無害なローカルreceiverだけです。
任意の外部宛先を許可するモードはありません。

## Windows IME＋NixOS on WSL

[Windows版の手順](windows/README.md)を参照してください。WindowsのWebView2を使う
専用ウィンドウにcode-serverを直接表示します。普段のEdgeやChromeへの設定変更はありません。

```text
Windowsの専用WebView2ブラウザ（Windows IME、Per-Monitor V2）
  └─ AppContainer（ネットワークcapabilityなし）
       └─ 専用SIDへのWFPルール＋同SIDだけのloopback例外
            └─ TCP 127.0.0.1:18081 → WSL側mitmproxy → code-server / preview
```

AppContainerのデータディレクトリだけに書き込みを許可し、実行ファイルは別の
読み取り専用ディレクトリへ置きます。ネットワークルールは永続化し、起動前に対象SID・
宛先・ポート・プロトコルを検証します。管理者権限が必要なのはWFPとloopback例外の
設定・解除です。ブラウザ自体は管理者として起動しません。

新規ウィンドウは同じAppContainerのWebView2へ割り当て、フォームPOSTも保持します。
外部プロトコル、ダウンロード、ページからの権限要求は拒否します。ホストオブジェクトや
Webメッセージ経由のローカルコード実行APIは公開しません。

[Windowsブラウザの証跡](evidence/windows-browser.json)では、プレビュー経由の通信が
許可時11件（faviconを含む）・制限時0件、実際のリモート拡張webviewのPOSTが
許可時1件・制限時0件でした。Markdownプレビューと統合ターミナルも検証しています。
[OS境界の証跡](evidence/windows-os-boundary.json)では、実際に待受中のTCP/UDPサーバーを
使い、proxy以外のloopbackとIPv6への通信が届かないことを確認しました。
UDPのsend成功は配送成功ではないため、受信側でも確認しています。

通常のEdgeはAppContainerで起動途中に`0xc0000005`、Chromeは`0x80000003`で終了しました。
この試行だけでそれらのブラウザが対応不可能とは判断していません。デモには実際に起動・
動作を確認できたWebView2を採用しています。

## 現時点の制約

- 製品のセキュリティ保証ではなく、境界の動作を確認するデモです。
- ブラウザ・カーネル・proxy自体の脆弱性はこの試験の範囲外です。
- Linux GUIを試した環境ではWSLgのWayland socketが接続拒否になり、
  sandboxの外でも同じ状態でした。Linux側のネイティブIMEは未検証です。
- localhostへのHTTPはブラウザのsecure contextとして扱われるため、デモでは
  CAの追加なしに動作します。EC2等への実運用では認証とTLSを別途設計します。
- code-serverとreceiverはホストに公開せず、proxyだけをloopbackに公開します。
  同じホスト上の他ユーザーを敵とする構成は未対応です。
- Marketplace、外部認証、任意のプレビュー先は既定で拒否します。
  許可先を増やすと、その宛先へコードや情報を送信できるようになります。
- WindowsのOSサービスによる代理通信や、全種類のCOM／外部プロトコルの脱出を
  網羅的に立証したものではありません。カーネルの遮断は専用SIDのプロセスを対象にします。
- サービスワーカー、WebRTC、WebTransport、全種類の外部プロトコルを個別に
  網羅した試験ではありません。proxy外の直接通信はnetwork namespaceで制限します。
- agent側のネットワークポリシーを置き換えるものではありません。
  このデモのバックエンドはDockerのinternal networkに閉じています。

## 停止

ブラウザの起動端末でCtrl+Cを押し、次を実行します。

```sh
docker compose -f demos/browser-ide/compose.yaml down
```

他のComposeプロジェクトには触れません。`workspace/`の編集内容は残ります。
