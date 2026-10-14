# code-server（Windows WebView2 + WSL）

agent内の `/workspace` を、Windowsの専用WebView2ウィンドウで編集します。
Windows IMEを直接使い、画面転送はしません。todeやnative VS Codeの改変版は使いません。

`.devcontainer/claude.local.toml`（チーム共通なら `claude.toml`）に設定します。

```toml
[codeServer]
enabled = true
extensions = ["anthropic.claude-code"]
```

既定は無効です。有効化後、`devcontainer up --workspace-folder . --remove-existing-container`
でagentを再作成してください。先に `devcontainer build --workspace-folder .` で
イメージをビルドしてください。
ホストの信頼できるチェックアウトから実行します。

```sh
.devcontainer/claude/scripts/code-server.sh prepare  # 初回。WindowsのUAC確認あり
.devcontainer/claude/scripts/code-server.sh open
```

`prepare` は固定ハッシュのWebView2 SDKとWindowsのC#コンパイラでクライアントを
ビルドし、専用AppContainerのWFPルールとloopback例外を設定します。
既存のWebView2 runtimeが必要です。ブラウザ本体は管理者権限で起動しません。
コード・バイナリは `%LOCALAPPDATA%\ClaudeCodeServerSetup` と `ClaudeCodeServerBin`、
書き込み可能なブラウザデータは `ClaudeCodeServerAC` に分離します。

```text
WebView2 (Windows IME)
  → AppContainer + WFP: TCP 127.0.0.1:18086のみ
  → code-server-proxy: ide.localhost:8000 / port-<port>.localhost:8000
  → agent:8088 の code-server → /workspace
```

agentの受信firewallも専用proxyのアドレスからの8088番だけを追加許可します。
code-server自体のホストポートは公開しません。agentの既存の送信制限は維持します。
新規ウィンドウは同じAppContainerに収め、外部アプリ呼び出し・ダウンロード・
ページからの権限要求は拒否します。起動時にWFPルールを検証し、不一致なら停止します。
通常ブラウザへのフォールバックはありません。

`codeServer.extensions` はtodeと同様、VS Code Marketplaceの拡張IDを指定します。
`@version` でバージョンを固定できます。ビルド時に専用ディレクトリへインストールし、
見つからない拡張や指定バージョンが入らなかった場合はビルドを失敗させます。
変更後はイメージを再ビルドし、agentを再作成してください。

ユーザー設定とUser配下の拡張の状態は専用volumeに保存します。
拡張本体はイメージに含め、再作成すると設定リストの内容に戻ります。
GitHubログイン・Settings Sync・実行時のMarketplaceアクセス・外部サイトのプレビューは未対応です。
フラグを無効にして環境を再作成するとサーバーとproxyは停止します。
ウィンドウも閉じてください。Windowsのルールを消す場合は、次を実行します。

```sh
.devcontainer/claude/scripts/code-server.sh remove-network
```

現状はWindows x64 + WSL向けで、Linuxクライアントは含みません。
18086番を固定で使うため、同時に開くworkspaceは一つです。
別workspaceへ切り替えるときはウィンドウを閉じ、以前の `code-server-proxy` を停止します。
同じWindowsプロファイルを再利用するため、workspace同士を相互に隔離する機能ではありません。
ブラウザproxyはローカル認証なしです。同一ホスト上の他ユーザーを敵とする環境や、
EC2等への公開用の認証・TLS構成には対応していません。

AppContainer/WFPはブラウザを乗っ取られた場合にも制約を残す境界ですが、
OSサービスによる代理操作やブラウザ・カーネルの脆弱性を網羅した保証ではありません。
起動スクリプトとproxyのコードはagentから書き換えられない場所に置く必要があります。
sharedモードの `/workspace/.devcontainer` は既存の設定で読み取り専用です。

## agent内のWebアプリを開く

agent内の3000番で起動したWebアプリは、隔離ウィンドウで
`http://port-3000.localhost:8000/` を開くと表示できます。code-serverの「ポート」から
開くリンクもこの形式になります。3000はアプリの待受ポートに置き換えてください。
ブラウザ用proxyの転送先は常にagentのcode-server（8088番）です。
そこからagent内のlocalhostへ転送するため、アプリ用のホストポート公開や
firewallへの追加ルールは不要です。8088番自身へのホスト名転送はループ防止のため拒否します。

アプリは `/` で公開され、`/assets/...` などのパスをそのまま使えます。
公開URLや許可ホストを検証するアプリでは、その設定は引き続き必要です。
例えばForgejoの設定は次になります（サブパスを付けません）。

```ini
[server]
HTTP_ADDR = 127.0.0.1
HTTP_PORT = 3000
ROOT_URL = http://port-3000.localhost:8000/
```

リンクは専用WebView2内で開いてください。普通のChrome向けにポートを公開する変更ではありません。
既存の `/proxy/<port>/` も利用できますが、その方式ではアプリ側にサブパスの設定が必要になる場合があります。

「Preview in Editor」はiframe内で表示します。アプリの埋め込み制限に加えて、
別サイトのiframeでは `SameSite=Lax` の認証Cookieを送信できないため、
ページを表示できてもログインできない場合があります。
ログインが必要なレビューなどは、専用WebView2の独立ウィンドウで開いてください。
