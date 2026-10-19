# ホストのIMEで使う隔離エディタ（Electronシェル）

agent内の`/workspace`を、ホストの画面とIMEで編集します。画面転送はしません。
agentの中でCoderのcode-serverを動かし、ホストでは専用のElectronシェルがそれを表示します。
WindowsとLinuxで、同じシェル、同じproxy、同じ設定を使います。
ホストのVS Codeをコンテナに接続する通常の方法が作る[経路](../../../docs/usage.md#vs-codeが作る経路)は、このシェルにはありません。
設計の経緯は[設計文書](../../../docs/superpowers/specs/2026-10-15-electron-shell-design.md)にあります。

```text
Electronシェル（ホストのIME）
  └─ すべての通信: code-server-proxy（ホストの127.0.0.1の18086〜18186番のうち1つ）
       ├─ http://localhost:18000       → agentのcode-server（IDEとwebview）
       └─ http://localhost:<port>      → agentの同じポートのWebアプリ
            agentのfirewallが、proxyからの接続をagentの127.0.0.1へ転送する
```

シェルは、`http://localhost:<port>`以外への通信と遷移を、ページの外側（Electronのブラウザプロセスとネットワークサービス）で拒否します。
proxyの許可リスト（`proxy.py`）も同じ宛先しか通しません。
proxyはagentとは別のコンテナで、agentからは書き換えられません。
`code-server.sh`が、composeが選んだ公開ポートを取得します。

## 有効にする

todeのサポートは削除しました。以前の設定に`[tode]`がある場合は、`claude.toml`と`claude.local.toml`からセクションごと削除し、必要な拡張機能を`codeServer.extensions`へ移してください。既存の`tode-user` volumeは自動削除しませんが、今後は使用しません。

1. `.devcontainer/claude.local.toml`（チーム共通なら `claude.toml`）に設定します。既定は無効です。

   ```toml
   [codeServer]
   enabled = true
   ```

2. イメージをビルドし、agentを再作成します。

   ```sh
   devcontainer build --workspace-folder .
   devcontainer up --workspace-folder . --remove-existing-container
   ```

3. ホストの信頼できるチェックアウトから起動します。`code-server.sh` はWSLならWindowsのシェルを、それ以外のLinuxならWaylandまたはX11でシェルを開きます。

   ```sh
   .devcontainer/claude/scripts/code-server.sh prepare  # Electronの取得だけ。省略すると下の起動時に取得します
   .devcontainer/claude/scripts/code-server.sh
   ```

どちらのOSでも管理者権限は使いません。
Electronは、`electron.py`が固定するバージョンのものを、Electronの`SHASUMS256.txt`のSHA-256と照合してから使います。

## シェルが拒否するもの

判定は`shell/main.js`と`shell/policy.js`にあり、ページのJavaScriptからは変更できません。
IDEのページはコンテナが配信するので、その中のスクリプトは攻撃者のものとして扱います。

| 経路 | 手段 |
| --- | --- |
| フレームのナビゲーションとサーバーのリダイレクト | `will-frame-navigate`と`will-redirect`、`webRequest`の応答ヘッダーで、許可したオリジン以外を拒否する |
| HTTPとWebSocket | すべてproxyへ送る（`<-loopback>`で、ホストのループバックへ直接送らない）。`webRequest`でも宛先を検査する |
| 名前解決 | `host-resolver-rules`で、proxyの127.0.0.1以外を解決しない |
| WebRTC | `disable_non_proxied_udp`で、UDPを使わせない |
| 新規ウィンドウ、外部プロトコル（`vscode://`など）、ダウンロード | 常に拒否する。外部アプリケーションは起動しない |
| 権限の要求 | クリップボードへの書き込みだけを許可する。読み取り、通知、位置情報、カメラなどは拒否する |
| WebHID、WebUSB、Web Serial、Web Bluetooth、画面の取得 | 常に拒否する |

開発者ツールとメニューはありません。
レンダラーはChromiumのサンドボックスで動かし、`--no-sandbox`は使いません。

## Linux（Wayland／X11）

ホストに必要なのはPython 3.11以上とWaylandまたはX11セッションです。
`WAYLAND_DISPLAY`が設定されていればWaylandを優先し、なければ`DISPLAY`を使ってX11で起動します。指定されたWaylandソケットが存在しない場合はエラーにします。
Waylandではtext-input-v3でホストのIMEを使います。X11では`DISPLAY`、`XAUTHORITY`、入力メソッドの環境変数を引き継ぎます。X11での日本語入力は実機未確認です。
両方が設定された環境でX11を選ぶ場合は、`env -u WAYLAND_DISPLAY .devcontainer/claude/scripts/code-server.sh`で起動できます（WSLではWindows版を起動します）。
`prepare` はElectronを `~/.cache/claude-devcontainer/electron/` に取得します。

`open` はElectronをホストで直接起動し、専用proxyの公開ポートに接続します。
Electronの起動後にコマンドは戻り、出力はworkspaceごとの状態ディレクトリ（`~/.local/state/claude-devcontainer/vscode/<workspaceのハッシュ>/`）の `client.log` に入ります。
同じworkspaceのウィンドウは一つまでで、2つ目の`open`はロックで止まります。別のworkspaceは同時に開けます。
プロファイルは従来と同じ状態ディレクトリの`home/shell`に保存し、ユーザー設定を引き継ぎます。

Chromiumのレンダラーsandboxは有効です。Electron全体を囲むnamespaceやファイルシステムの隔離はありません。
接続先と外部アプリケーションの起動は、シェルの判定とproxyの許可リストで制限します。
Electronのブラウザプロセスが乗っ取られた場合に、ホストのファイルやネットワークへのアクセスを防ぐ外側の壁はありません。

AppArmorが非特権のuser namespaceを制限する（`kernel.apparmor_restrict_unprivileged_userns=1`）ホストでは、ChromiumのsandboxのためにElectronへuser namespaceを許可するAppArmorプロファイルが必要になる場合があります。
`prepare`はこの制限を検出すると警告を表示します。`--no-sandbox`は使いません。

## Windows（WSL）

ホスト側のスクリプト（認証情報の注入、gitの転送、イメージの読み込み）とDockerはWSL2で動かし、シェルだけをWindowsで動かします。
シェルは、WSL2がWindowsの127.0.0.1へ転送するproxyのポートに接続します。
workspaceごとに公開ポートとプロファイルが分かれるので、複数のworkspaceを同時に開けます。

Windows側のファイルは `%LOCALAPPDATA%\ClaudeDevcontainer` に置きます。agentからは届きません。

| 場所 | 内容 |
| --- | --- |
| `electron\<バージョン>-win32-x64` | Electron |
| `workspaces\<workspaceのハッシュ>\shell` | シェルのコピー。`open`のたびに書き直す |
| `workspaces\<workspaceのハッシュ>\profile` | シェルのプロファイル（エディタのユーザー設定と状態） |
| `workspaces\<workspaceのハッシュ>\client.log` | シェルの出力 |

Windowsでも、Electron全体を囲むOSの隔離はありません。
AppContainerやWFPでElectronを閉じ込めるには、ループバック免除とWFPのルールに管理者権限が要るためです。
そのため、Windowsで接続先を制限するのは、シェルの判定とproxyの許可リストだけです。
レンダラーの脱出とブラウザプロセスの奪取を連鎖させる攻撃まで想定する場合は、Windows Sandboxでシェルを動かしてください。

## agent側のserver

agentでは、`agent/start-code-server.sh`がcode-serverを127.0.0.1の18000番で起動します。
ホストのポートには公開しません。

- code-server 4.141.0は、`agent/Dockerfile`がバージョンとSHA-256を固定してビルド時に入れます。`codeServer.enabled`が有効な場合だけ取得します。
- Claude Codeの拡張（`anthropic.claude-code`）は常に入れます。全ワークスペースで同じimageレイヤーを共有するように、`codeServer.extensions`とは別に入れます。
- `codeServer.extensions` は、ビルド時にcode-serverのCLIで、標準のOpen VSXから入れます。Open VSXで公開されている拡張とバージョンを指定してください。指定した拡張やバージョンが入らなければビルドは失敗します。拡張は起動のたびに`~/.vscode-server/extensions`へコピーし直します。
- 起動のたびに64桁の16進トークンを作り、従来と同じ`/run/claude-devcontainer/vscode-token`に置きます。serverには`HASHED_PASSWORD`として渡し、シェルは18000番へのすべての要求に`code-server-session` Cookieとして付けます。Cookieなしで取得されるService Workerのスクリプトも含みます。最初のURLにはフォルダだけを指定します。
- webviewはIDEと同じ`localhost:18000`から配信されます。serverへのパッチとwebview専用ポートは不要です。
- Workspace Trustは`--disable-workspace-trust`で無効にします。agentのworkspaceはもともと攻撃者が書き換えられるもので、agentのClaude Codeも最初から信頼しています。制限モードはagent内の拡張機能を止めるだけで、ホストを守りません。
- 更新するときはDockerfileのcode-serverのバージョンとハッシュを変え、ワークベンチ、webview、Simple Browser、ポートビューを実際に確認してください。
- agentのfirewallは、code-server-proxyからの接続をすべてagentの127.0.0.1の同じポートへ転送します（`agent/init-firewall.sh`）。これにはcomposeが設定する`net.ipv4.conf.all.route_localnet=1`が必要です。ほかの送信元からの接続は、INPUTの既定のDROPで拒否されます。
- 拡張の状態は`vscode-server-data` volumeに残ります。Machine設定は、起動のたびにimageの内容（ターミナルを`/bin/bash`で起動する設定と、localhostのリンクをSimple Browserで開く設定）に戻ります。ユーザー設定は、ブラウザ版VS Codeの仕様でシェルのプロファイルに保存されます。

## agent内のWebアプリを開く

agent内の3000番で起動したWebアプリは、エディタのSimple Browserで `http://localhost:3000/` を開くと表示できます。
3000はアプリの待受ポートに置き換えてください。
URLはagentの中と同じなので、公開URLを検証するアプリでも設定は変えずに済みます。
例えばForgejoの設定は次のままで動きます。

```ini
[server]
HTTP_ADDR = 127.0.0.1
HTTP_PORT = 3000
ROOT_URL = http://localhost:3000/
```

アプリが127.0.0.1と0.0.0.0のどちらで待ち受けていても接続できます。
ホストへのポート公開やfirewallへの追加は要りません。
18000番はエディタが使うので、アプリには使えません。

Simple Browserは、アプリをwebviewのiframeのさらに内側のiframeで表示します。
IDE、webview、アプリはすべて`localhost`の同じサイトなので、ログインの`SameSite=Lax`のCookieも送られます。
proxyは、アプリの応答から`X-Frame-Options`とCSPの`frame-ancestors`を取り除き、iframeでの表示を許します。
アプリはagentの中で動くので、埋め込みの制限を外しても守るものは減りません。

エディタやターミナルで `http://localhost:<port>` のリンクをクリックすると、Simple Browserで開きます（Machine設定の `workbench.externalUriOpeners`）。
シェルは新しいウィンドウを開かないので、この設定がなければクリックしても何も起きません。
拡張機能が `vscode.env.openExternal` で開くリンクには、この設定は効きません。Claude Codeのチャット欄のリンクがどちらで開かれるかは、まだ確かめていません。

ポートビューやSimple Browserが生成する`http://localhost:18000/proxy/<port>/...`には、信頼するproxyが`http://localhost:<port>/...`への302を返します。`/absproxy/<port>/...`も同じように扱います。パスとクエリを保ち（`/proxy/3000?x=1`も`http://localhost:3000/?x=1`へ転送）、code-serverのパス中継を通さないので、アプリの絶対パスも同じオリジンで動きます。`VSCODE_PROXY_URI`や`--proxy-domain`は指定しません。

## 無効にする

フラグを無効にして環境を再作成すると、serverとproxyは停止します。ウィンドウも閉じてください。

## 制約

- Windows・Linuxとも、Electron全体を囲むOSの隔離はありません。通信先の制限はシェルの判定とproxyの許可リストに依存します。
- Electronのセキュリティ修正に追従するため、`electron.py`のバージョンとハッシュを更新し続ける必要があります。
- GitHubへのログイン、Settings Sync、実行時のMarketplaceへの接続、外部サイトのプレビューは使えません。
- Ctrl-V・Ctrl-Shift-V・Shift-Insertは、シェルが通常のpasteイベントとしてフォーカス中のフレームへ渡します。Terminalでも同じ経路を使います。Clipboard APIの読み取りは引き続き拒否するため、IDEの右クリックメニューからの貼り付けは使えません。
- すべてのwebviewが同じオリジン（`localhost:18000`）になります。VS Codeの既定では、webviewごとにオリジンが分かれます。
- Cookieはポートを区別しないので、IDEの接続トークンのCookieは、agent内のWebアプリにも送られます。agentはもともとIDEのserverを動かしているので、トークンを得ても新たにできることはありません。
- proxyはローカル認証なしです。同一ホスト上の他ユーザーを敵とする環境や、EC2などへ公開するための認証やTLSの構成には対応していません。接続トークンは、ホストの同じユーザーなら読めます。
- 起動スクリプト、シェル、proxyのコードは、agentから書き換えられない場所に置く必要があります。sharedモードの `/workspace/.devcontainer` は既存の設定で読み取り専用です。
