# ホストのIMEで使う隔離エディタ（Electronシェル）

agent内の`/workspace`を、ホストの画面とIMEで編集します。画面転送はしません。
agentの中で公式のVS Code（`code serve-web`）を動かし、ホストでは専用のElectronシェルがそれを表示します。
WindowsとLinuxで、同じシェル、同じproxy、同じ設定を使います。
ホストのVS Codeをコンテナに接続する通常の方法が作る[経路](../../../docs/usage.md#vs-codeが作る経路)は、このシェルにはありません。
設計の経緯は[設計文書](../../../docs/superpowers/specs/2026-10-15-electron-shell-design.md)にあります。

```text
Electronシェル（ホストのIME）
  └─ すべての通信: code-server-proxy（ホストの127.0.0.1の18086〜18186番のうち1つ）
       ├─ http://localhost:18000       → agentのcode serve-web（IDE）
       ├─ http://localhost:18001       → 同じserver（webview）
       └─ http://localhost:<port>      → agentの同じポートのWebアプリ
            agentのfirewallが、proxyからの接続をagentの127.0.0.1へ転送する
```

シェルは、`http://localhost:<port>`以外への通信と遷移を、ページの外側（Electronのブラウザプロセスとネットワークサービス）で拒否します。
proxyの許可リスト（`proxy.py`）も同じ宛先しか通しません。
proxyはagentとは別のコンテナで、agentからは書き換えられません。
`code-server.sh`が、composeが選んだ公開ポートを取得します。

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

3. ホストの信頼できるチェックアウトから起動します。`code-server.sh` はWSLならWindowsのシェルを、それ以外のLinuxならbubblewrapに入れたシェルを開きます。

   ```sh
   .devcontainer/claude/scripts/code-server.sh prepare  # Electronの取得だけ。省略するとopenが取得します
   .devcontainer/claude/scripts/code-server.sh open
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

## Linux（bubblewrap）

ホストに必要なのは `bwrap`（bubblewrap）、Python 3.11以上、Waylandセッションです。
X11は渡さないので、X11セッションでは起動しません。
`prepare` はElectronを `~/.cache/claude-devcontainer/electron/` に取得し、これらを確認して表示します。

`open` は、シェルをbubblewrapの中で起動して、ウィンドウが開いた時点で戻ります。
中継を持つプロセスは切り離されて残り、その出力はworkspaceごとの状態ディレクトリ（`~/.local/state/claude-devcontainer/vscode/<workspaceのハッシュ>/`）の `client.log` に入ります。
同じworkspaceのウィンドウは一つまでで、2つ目の`open`はロックで止まります。別のworkspaceは同時に開けます。

```text
Electronシェル（ホストのIME。Waylandのtext-input-v3）
  └─ bubblewrap: user/pid/net/ipc/uts namespaceを分離、capabilityなし
       ├─ /usr, /lib, /nix/store, /etc/fonts を読み取り専用で共有
       ├─ Electronとシェルのコピーを読み取り専用で共有
       ├─ 専用home（状態ディレクトリのhome）にシェルのプロファイル
       ├─ Wayland socketだけを共有（home、D-Bus、X11、SSH agent、Docker socketは渡さない）
       └─ network namespaceはloopbackのみ
            127.0.0.1:18086 → Unix socket → ホストの127.0.0.1:<公開ポート>（code-server-proxy）
```

- シェルの判定に加えて、OSの層でも外へ出られません。network namespaceにはループバックしかなく、ルーティングできる宛先がありません。
- サンドボックスの中では、起動前に自分のnetwork namespaceがホストと違うこと、外部アドレスに届かないこと、ホストのhomeが見えないことを確かめ、違えば起動しません。
- `xdg-open` は拒否するスクリプトに置き換えています。
- GPUは渡しません（`--disable-gpu`）。

Chromiumのサンドボックスは、bubblewrapのuser namespaceの中に、さらにuser namespaceを作ります。
Ubuntu 23.10以降のように、AppArmorが非特権のuser namespaceを制限する（`kernel.apparmor_restrict_unprivileged_userns=1`）ホストでは、bubblewrapとElectronにuser namespaceを許可するAppArmorプロファイルが必要です。
プロファイルの導入には管理者権限が要ります。
`prepare`はこの制限を検出すると警告を表示します。
この構成での起動は、まだ確認していません。

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

Windowsには、Linuxのbubblewrapに当たるOS層がありません。
AppContainerやWFPでElectronを閉じ込めるには、ループバック免除とWFPのルールに管理者権限が要るためです。
そのため、Windowsで接続先を制限するのは、シェルの判定とproxyの許可リストだけです。
レンダラーの脱出とブラウザプロセスの奪取を連鎖させる攻撃まで想定する場合は、Windows Sandboxでシェルを動かしてください。

以前のWebView2クライアントを使っていた場合は、そのWFPのルールとループバック免除を次で消せます（UACの確認があります）。

```sh
.devcontainer/claude/scripts/code-server.sh remove-network
```

## agent側のserver

agentでは、`agent/start-code-server.sh`が`code serve-web`を127.0.0.1の18000番で起動します。
ホストのポートには公開しません。

- VS CodeのCLIとweb版serverは、`agent/Dockerfile`がcommitとSHA-256を固定してimageのビルド時に入れます。serve-webは起動時にserverをダウンロードしようとしますが、同じcommitのserverを見つけるとダウンロードしません。agentは外部に接続できないので、取得はビルド時に限ります。
- `codeServer.extensions` は、ビルド時にserverのCLIでMarketplaceから入れます。見つからない拡張や、指定したバージョンが入らなかった場合はビルドが失敗します。拡張は起動のたびに`~/.vscode-server/extensions`へコピーし直すので、リストの内容に戻ります。
- 接続トークンはagentの起動のたびに作り直し、`/run/claude-devcontainer/vscode-token`に置きます。`open`がこれを読んでシェルに渡し、シェルはserver（18000番と18001番）へのすべての要求にトークンのCookieを付けます。webviewのService Workerのスクリプトのように、ページがCookieを付けずに送る要求も含みます。
- VS Codeのweb版は、webviewのページを`*.vscode-cdn.net`から読み込みます。この構成では`localhost:18001`から配信するため、`agent/vscode-patches.py`がビルド時にserverの3つのファイルを書き換えます。serve-webはすべての応答に、別のオリジンのiframeに入れることを禁じるヘッダーを付けます。ポートが違えばオリジンも違うので、proxyは18001番の応答だけ、IDE（18000番）のiframeに入れることを許すよう書き換えます。
- Workspace Trustは無効にしてあり、制限モードにはなりません。agentのworkspaceはもともと攻撃者が書き換えられるもので、agentのClaude Codeも最初から信頼しています。制限モードは、agentの中の拡張機能を止めるだけで、ホストを守りません。serve-webにはserverへこの指定を渡す方法がないので、同じスクリプトがserverの起動スクリプトに`--disable-workspace-trust`を加えます。
- VS Codeを更新するときは、Dockerfileのcommitとハッシュを変えてください。`agent/vscode-patches.py`が書き換える文字列が見つからなければ、ビルドが失敗します。
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
18000番と18001番はエディタが使うので、アプリには使えません。

Simple Browserは、アプリをwebviewのiframeのさらに内側のiframeで表示します。
IDE、webview、アプリはすべて`localhost`の同じサイトなので、ログインの`SameSite=Lax`のCookieも送られます。
proxyは、アプリの応答から`X-Frame-Options`とCSPの`frame-ancestors`を取り除き、iframeでの表示を許します。
アプリはagentの中で動くので、埋め込みの制限を外しても守るものは減りません。

エディタやターミナルで `http://localhost:<port>` のリンクをクリックすると、Simple Browserで開きます（Machine設定の `workbench.externalUriOpeners`）。
シェルは新しいウィンドウを開かないので、この設定がなければクリックしても何も起きません。
拡張機能が `vscode.env.openExternal` で開くリンクには、この設定は効きません。Claude Codeのチャット欄のリンクがどちらで開かれるかは、まだ確かめていません。

serve-webのVS Codeにはポートを転送する仕組みがないので、ポートビューは表示されず、ポートの自動転送も起きません。アプリは転送しなくても、上のURLで開けます。

## 無効にする

フラグを無効にして環境を再作成すると、serverとproxyは停止します。ウィンドウも閉じてください。

## 制約

- Windowsでは、シェルの判定とproxyの許可リストのほかに壁がありません。Linuxでは、その外側にbubblewrapがあります。
- Electronのセキュリティ修正に追従するため、`electron.py`のバージョンとハッシュを更新し続ける必要があります。
- GitHubへのログイン、Settings Sync、実行時のMarketplaceへの接続、外部サイトのプレビューは使えません。
- 右クリックメニューからの貼り付けは、クリップボードの読み取り権限が要るので動きません。キーボードでの貼り付けとコピーは動きます。
- すべてのwebviewが同じオリジン（`localhost:18001`）になります。VS Codeの既定では、webviewごとにオリジンが分かれます。
- Cookieはポートを区別しないので、IDEの接続トークンのCookieは、agent内のWebアプリにも送られます。agentはもともとIDEのserverを動かしているので、トークンを得ても新たにできることはありません。
- proxyはローカル認証なしです。同一ホスト上の他ユーザーを敵とする環境や、EC2などへ公開するための認証やTLSの構成には対応していません。接続トークンは、ホストの同じユーザーなら読めます。
- 起動スクリプト、シェル、proxyのコードは、agentから書き換えられない場所に置く必要があります。sharedモードの `/workspace/.devcontainer` は既存の設定で読み取り専用です。
