# WindowsのAppContainer＋WFPで開くcode-server

Windows 11 Home（10.0.26200）、WebView2 runtime 155.0.4283.45、Windows IME、
NixOS on WSLで検証したデモです。画面転送、Sandboxie、追加カーネルドライバは使いません。
WebView2 SDK 1.0.4258.31だけを固定ハッシュで取得し、既存のWebView2 runtimeと
.NET FrameworkのC#コンパイラを利用します。

## 起動

WSL側でバックエンドを起動します。

```sh
docker compose -f demos/browser-ide/compose.yaml up -d
```

信頼できるホスト側のチェックアウトから、Windows PowerShellで実行します。
`prepare.ps1`は起動コードを`%LOCALAPPDATA%\BrowserIdeDemoSetup`に配置します。
これらの起動コードや設定を、実行対象のagentに書き込み可能な共有領域として渡さないでください。

```powershell
# このディレクトリへ移動したWindows PowerShellで実行。初回はUAC確認が出ます。
.\prepare.ps1 -ConfigureNetwork
& "$env:LOCALAPPDATA\BrowserIdeDemoSetup\launch.ps1"
```

以後は`launch.ps1`で起動できます。セットアップ済みの同じマシンでは
`prepare.ps1`に`-ConfigureNetwork`を付けずにビルドだけを行えます。
更新ビルドの前には、このデモの最終版ウィンドウを閉じてください。

WindowsからWSLの`127.0.0.1:18081`へ接続できる必要があります。
普通のブラウザでこのproxyを使うだけでは、このデモの隔離にはなりません。

## 使い方

- `ime.md`を開いて普段のWindows IMEで入力します。
- `README.md`を開き、コマンドパレットの`Markdown: Open Preview`で表示します。
- Markdown内のリンクから通信試験ページを開き、POSTや別ウィンドウを試します。
- `Demo: Open Boundary Probe`でリモート拡張のwebviewを表示します。
- 統合ターミナルでは`node hello.js`を実行できます。

別タブを要求するリンクは、同じ隔離内の別ウィンドウとして開きます。
外の既定ブラウザやアプリを呼び出しません。ダウンロードや外部アプリ連携は拒否します。

高DPI表示にはPer-Monitor V2を使います。この端末では144 DPI、150%表示を記録しました。
最初の試作の96 DPI固定表示は修正しています。IMEは利用者による実操作で確認されており、
自動入力テストをIME検証の代わりにはしていません。

## 検証

Windows PowerShell:

```powershell
& "$env:LOCALAPPDATA\BrowserIdeDemoSetup\verify-boundary.ps1"
```

無害な専用ファイル、TCP/UDP待受、一時的な`browseridedemo:`プロトコル登録を使い、
通常実行とAppContainer内の実行を比較します。プロトコル登録は試験後に削除します。
既に同名の登録がある場合は上書きせず停止します。

WSL:

```sh
python3 demos/browser-ide/verify-windows.py
```

ブラウザの画面を実際に開き、許可／拒否の両方で以下を確認します。

- フォームPOSTの本文、リンク、新規ウィンドウ、リダイレクト、fetch、beacon、画像。
- 外部スキームへの遷移の取り消し。
- Markdownプレビュー、コンテナ内ターミナルでのNode実行（通常のキー押下・解放イベントを使用し、毎回新しい結果ファイルを確認）。
- リモート拡張webviewからのPOST。

WFPによるproxy以外の遮断は両モードで維持します。許可モードで増えるのは
proxyから無害なローカルreceiverへの経路だけです。結果は
`/tmp/browser-ide-windows-evidence/browser-verification.json`に出力します。
通常終了と試験失敗ではproxyを拒否モードへ戻します。強制終了時は次で戻せます。

```sh
DEMO_CONTROL=0 docker compose -f demos/browser-ide/compose.yaml up -d proxy
```

## 実装した境界

`AppContainer.cs`はネットワークcapabilityを付けずにプロセスを作成します。
実行ファイル側でも期待したAppContainer SIDであることを確認し、隔離外での直接起動を拒否します。
WebView2のブラウザ・GPU・ネットワーク・rendererプロセスにも同じSIDが継承されることを実測しました。

`ProxyFirewall.cs`は、そのSIDだけに四つの永続WFP遮断ルールを作ります。
IPv4のproxy以外のアドレス、proxy以外のポート、TCP以外のプロトコル、IPv6を遮断します。
普通のEdgeや端末全体のFirewall既定値は変更しません。起動時の照合に失敗すれば起動しません。

ルールを通常ユーザーから読み取るACLを付けますが、変更権限は追加しません。
AppContainerのloopback例外は同SIDだけに設定し、WFPのルールを先に作成します。
設定解除では逆にloopback例外を先に削除します。

これは上記の経路についてのデモです。全Windowsサービスの代理通信、全COMインターフェース、
ブラウザやOSの脆弱性を網羅した保証ではありません。外部プロトコルのOS側試験は専用の
無害なハンドラーを用いた一例であり、ブラウザでは別途ナビゲーションと外部スキームを拒否します。
通常のデスクトップVS Codeのリモート接続を安全化する変更でもありません。

## 停止・ネットワーク設定の解除

ウィンドウを閉じるとそのUIは終了します。WFP設定は意図的に残ります。
解除は管理者のWindows PowerShellから行います。

```powershell
& "$env:LOCALAPPDATA\BrowserIdeDemoSetup\configure-network.ps1" -Remove
```

デモ専用の四つのWFPフィルター、専用sublayer、同SIDのloopback例外だけを削除します。
再設定するまで`launch.ps1`は停止します。ブラウザのデータや編集中のソースは削除しません。

## 参照

- [Microsoft: Launch an AppContainer](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)
- [Microsoft: WebView2 security](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/security)
- [Microsoft: FwpmFilterAdd0](https://learn.microsoft.com/en-us/windows/win32/api/fwpmu/nf-fwpmu-fwpmfilteradd0)
