# ホストのIMEで使う隔離エディタ（Windows WebView2 / Linux VS Code）

agent内の `/workspace` を、ホストの画面とIMEで編集します。画面転送はしません。
ホストのVS Codeをコンテナに接続する通常の方法が作る[経路](../../../docs/usage.md#vs-codeが作る経路)は、どちらのクライアントにもありません。

| ホスト | クライアント | agent側 |
| --- | --- | --- |
| Windows x64 + WSL | 専用AppContainerのWebView2ウィンドウ | code-server（8088番） |
| Linux（Wayland） | bubblewrapに入れたホストのVS Code | ホストのVS Codeと同じcommitの公式VS Code server（8089番） |

どちらも通信は専用のproxy（`code-server-proxy`）だけを通り、proxyのallowlist（`proxy.py`）が宛先を決めます。
proxyはホストの127.0.0.1の18086〜18186番のうち空いている1つに公開され、`code-server.sh`がその番号をcomposeから取ります。

## 有効にする

1. `.devcontainer/claude.local.toml`（チーム共通なら `claude.toml`）に設定します。既定は無効です。

   ```toml
   [codeServer]
   enabled = true
   extensions = ["anthropic.claude-code"]
   ```

2. イメージをビルドし、agentを再作成します。

   ```sh
   devcontainer build --workspace-folder .
   devcontainer up --workspace-folder . --remove-existing-container
   ```

3. ホストの信頼できるチェックアウトから起動します。`code-server.sh` はWSLならWindows版、それ以外のLinuxならLinux版を開きます。

   ```sh
   .devcontainer/claude/scripts/code-server.sh prepare  # Windowsは初回のみ（UAC確認あり）。Linuxは環境の確認だけ
   .devcontainer/claude/scripts/code-server.sh open
   ```

## Windows（WebView2 + WSL）

`prepare` は固定ハッシュのWebView2 SDKとWindowsのC#コンパイラでクライアントを
ビルドし、専用AppContainerのWFPルールとloopback例外を設定します。
既存のWebView2 runtimeが必要です。ブラウザ本体は管理者権限で起動しません。
Windows側のファイルは次のように分離します。

| 場所（`%LOCALAPPDATA%` 配下） | 内容 |
| --- | --- |
| `ClaudeCodeServerSetup` | セットアップ用スクリプトとSDK |
| `ClaudeCodeServerBin` | ビルドしたブラウザ（AppContainerからは読み取り・実行のみ） |
| `ClaudeCodeServerAC` | 書き込み可能なブラウザデータ |

```text
WebView2 (Windows IME)
  → AppContainer + WFP: TCP 127.0.0.1:18086のみ
  → code-server-proxy: ide.localhost:8000 / port-<port>.localhost:8000
  → agent:8088 の code-server → /workspace
```

- 新規ウィンドウは同じAppContainerに収めます。外部アプリ呼び出し・ダウンロード・
  ページからの権限要求は拒否します。
- 起動時にWFPルールを検証し、不一致なら停止します。通常ブラウザへのフォールバックはありません。
- `codeServer.extensions` はビルド時にcode-serverの専用ディレクトリへインストールし、
  見つからない拡張や指定バージョンが入らなかった場合はビルドを失敗させます。
  変更後はイメージを再ビルドし、agentを再作成してください。
  ユーザー設定とUser配下の拡張の状態は専用volumeに保存します。
  拡張本体はイメージに含め、再作成すると設定リストの内容に戻ります。
- Windowsのルールを消す場合は `code-server.sh remove-network` を実行します。

## Linux（bubblewrap + VS Code）

ホストに必要なのは `bwrap`（bubblewrap）、VS Code（`code`。別の場所なら `codeServer.vscode`）、
Python 3.11以上、Waylandセッションです。X11は渡さないので、X11セッションでは起動しません。
`prepare` はこれらを確認して表示するだけで、管理者権限は使いません。

`open` は次の順で動きます（`linux/client.py` が `linux/server.py` を呼びます）。
同じworkspaceのウィンドウが開いている間は、serverを起動し直す前にロックで止まります。

1. ホストの `code --version` のcommitに合う公式VS Code serverをホストで取得し、
   `~/.cache/claude-devcontainer/vscode-server/<commit>/` に保存します。
   VS Codeは自分と同じcommitのserverにしか接続しないため、イメージには入れません。
2. `codeServer.extensions` を、そのserverのCLIでホストからMarketplaceに取りに行き、
   同じキャッシュに入れます。リストが変わったときだけ入れ直します。agentはMarketplaceに出られません。
3. serverと拡張を `docker compose cp` でagentの `vscode-server` volume（`/opt/vscode-server`）へ入れます。
   rootの所有なので、agentは書き換えられません。
4. 接続トークンを新しく作り、agentの `/run/claude-devcontainer/vscode-token` と
   ホストのworkspaceごとの状態ディレクトリ（`~/.local/state/claude-devcontainer/vscode/<workspaceのハッシュ>/token`）に置きます。
5. agentで `node` としてserverを8089番で起動します。拡張は起動のたびに
   `~/.vscode-server/extensions` へコピーし直すので、リストの内容に戻ります。
   データ（Machine設定、拡張の状態）は `vscode-server-data` volumeに残ります。
6. ホストのVS Codeをbubblewrapの中で起動します。`open`はウィンドウが起動した時点で戻り、
   中継を持つプロセスは切り離されて残ります。その出力は状態ディレクトリの `client.log` に入ります。

```text
VS Code（ホストのIME。Waylandのtext-input-v3）
  └─ bubblewrap: user/pid/net/ipc/uts namespaceを分離、capabilityなし
       ├─ /usr, /lib, /nix/store, /etc/fonts を読み取り専用で共有
       ├─ 専用home（~/.local/state/claude-devcontainer/vscode/<workspaceのハッシュ>/home）にuser-dataと拡張
       ├─ Wayland socketだけを共有（home、D-Bus、X11、SSH agent、Docker socketは渡さない）
       └─ network namespaceはloopbackのみ
            127.0.0.1:18086 → Unix socket → ホストの127.0.0.1:<公開ポート>（code-server-proxy）
              → vscode.localhost:8000 → agent:8089 の VS Code server → /workspace
```

- resolver拡張（`linux/resolver/`）が `vscode-remote://claude-devcontainer+agent/workspace` を
  解決します。VS Codeが送るupgrade要求を `http://vscode.localhost:8000/...` へのproxy要求に書き換え、
  101のあとはproxyが生のストリームを通します（`websocket=false`）。
  VS Codeのproposed API（`resolvers`）を使うので、`--enable-proposed-api` 付きで起動します。
  Dev Containers拡張とDockerは使いません。
- サンドボックス内のVS Codeは、起動前に自分のnetwork namespaceがホストと違うこと、
  外部アドレスに届かないこと、ホストのhomeが見えないことを確かめ、違えば起動しません。
- リモート拡張が開く「ローカル」ターミナルや `code --openExternal` は、このサンドボックスの中で動きます。
  `xdg-open` は拒否するスクリプトに置き換えています。
- GPUは渡しません（`--disable-gpu`）。ホストのVS Codeの設定・拡張とは別の、workspaceごとのuser-dataを使います。
- ホストのVS Codeを更新すると、次の `open` で新しいserverを取得します。
- 同じworkspaceの2つ目のウィンドウは開けません（状態ディレクトリの `lock`）。別のworkspaceは同時に開けます。
- Linuxでもcode-server（8088番）はagentで動きますが、Linuxクライアントは使いません。

## 無効にする

フラグを無効にして環境を再作成するとサーバーとproxyは停止します。
ウィンドウも閉じてください。Windowsのルールを消す場合は、次を実行します。

```sh
.devcontainer/claude/scripts/code-server.sh remove-network
```

## 制約

- GitHubログイン・Settings Sync・実行時のMarketplaceアクセス・外部サイトのプレビューは未対応です。
- Windowsクライアントは18086番だけを許可するため、同時に開くworkspaceは一つです。
  別workspaceへ切り替えるときはウィンドウを閉じ、以前の `code-server-proxy` を停止します。
- Windowsでは同じプロファイルを再利用するため、workspace同士を相互に隔離する機能ではありません。
  Linuxではuser-dataをworkspaceごとに分けますが、サンドボックスの構成は同じです。
- proxyはローカル認証なしです。同一ホスト上の他ユーザーを敵とする環境や、
  EC2等への公開用の認証・TLS構成には対応していません。Linuxのserverの接続トークンは
  ホストの同じユーザーなら読めます。
- AppContainer/WFPとbubblewrapは、クライアントを乗っ取られた場合にも制約を残す境界ですが、
  OSサービスによる代理操作やカーネル・compositorの脆弱性を網羅した保証ではありません。
- 起動スクリプトとproxyのコードはagentから書き換えられない場所に置く必要があります。
  sharedモードの `/workspace/.devcontainer` は既存の設定で読み取り専用です。
- Linuxのserverと拡張の取得はホストのネットワークで行い、proxyの制限を受けません。

## agent内のWebアプリを開く

agent内の3000番で起動したWebアプリは、Windowsの隔離ウィンドウで
`http://port-3000.localhost:8000/` を開くと表示できます。code-serverの「ポート」から
開くリンクもこの形式になります。3000はアプリの待受ポートに置き換えてください。
ブラウザ用proxyの転送先は常にagentのcode-server（8088番）です。
そこからagent内のlocalhostへ転送するため、アプリ用のホストポート公開や
firewallへの追加ルールは不要です。8088番と8089番自身へのホスト名転送はループ防止のため拒否します。

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

LinuxのVS Codeでは、VS Codeのポート転送はサンドボックス内のlocalhostに届きます。
「ブラウザで開く」はサンドボックスの外に出ないので使えず、押しても何も起きません。エディタ内のSimple Browserで開いてください。
