# テストと動作確認の結果

2026-10-12にagentの中（DinD）で行った、デモアプリのテストとcompose stackの確認結果です。

## テスト

| 対象 | 結果 | 内容 |
| --- | --- | --- |
| frontend | 1 file, 1 test passed | [frontend/test/containers.test.js](../frontend/test/containers.test.js)。busyboxコンテナのHTTP応答（`dind-ok`）を確認。テスト本体は549ms、vitest全体は812ms |
| backend | BUILD SUCCESSFUL。1 test, 0 failures, 0 errors, 0 skipped | [MessageRepositoryTest.java](../backend/src/test/java/demo/MessageRepositoryTest.java)。`readsTheSeededMessage`が2.071秒で成功。内訳は Ryuk の起動約0.46秒、postgres の起動約1.06秒 |

frontendの実行時に`node_modules are out of sync with your lockfile`のWARNが出ましたが、テストは通りました。`pnpm-workspace.yaml`の`verifyDepsBeforeRun: warn`による表示です。

テストは各1件で、検証範囲は限られます。frontendはReactのコンポーネントを、backendはJavalinのルートとMain.javaを検証していません。

## 実行環境

- DinDのDockerは29.7.2で、`tcp://127.0.0.1:2375`で接続されました。
- DinDには次のイメージが読み込まれていて、テスト中にpullは発生しませんでした。`busybox:1.37`、`eclipse-temurin:17-jre-alpine`、`nginx:1.27-alpine`、`postgres:16-alpine`、`testcontainers/ryuk:0.14.0`。
- Ryukは`testcontainers.properties`の指定どおり0.14.0が使われました。

## compose stack

- `pnpm -C frontend build`と`./gradlew --offline installDist`は成功しました。
- `docker compose up -d --build`で、db、backend、frontendが起動しました。dbとbackendはhealthyになりました。
- `curl http://127.0.0.1:3000/api/message`は`{"message":"hello from postgres"}`を返しました。`http://127.0.0.1:3000/`は200を返しました。

## 画面のスクリーンショット

- `http://127.0.0.1:3000/`の表示は、見出し「claude-devcontainer demo」と本文「backend says: hello from postgres」でした。
- 保存したスクリーンショットは[demo-compose.png](../.local/tmp/claude-1000/-workspace/40e692b9-cf2a-4dc8-a924-f66ca82d2558/scratchpad/demo-compose.png)です。
- 最初に待たずに撮った画像は、描画前の状態でした。後で待ってから撮り直し、同じファイル名で上書きしました。

## 停止

- `docker compose down`で、frontend、backend、dbのコンテナと`workspace_default`ネットワークが削除されました。
- 停止後、`docker ps -a`にコンテナは残っていませんでした。

## VS Codeのリモート拡張機能からホストでコマンドを実行

2026-10-14に、[VS Codeが作る経路](usage.md#vs-codeが作る経路)のうち、リモート拡張機能からホスト側のターミナルを開き、コマンドを実行する経路を実機で確認しました。

### 条件と手順

- ホストはLinux（NixOS）、VS Code 1.119.0（commit `8b640eef5a6c6089c029249d48efa5c99adf7d51`）、Dev Containers 0.469.0です。Windowsでは未検証です。
- 稼働中のagentコンテナへ「Attach to Running Container」に相当する接続を使いました。拡張機能から見た`vscode.env.remoteName`は`attached-container`でした。
- ホストのuser-dataとローカル拡張機能のディレクトリは、検証専用の一時ディレクトリに分けました。Workspace Trustはこの検証用設定で無効にしました。Restricted Modeでの検証ではありません。
- 検証用拡張機能をコンテナの`node`（UID 1000）でインストールしました。`extensionKind: ["workspace"]`、`activationEvents: ["onStartupFinished"]`を指定し、`~/.vscode-server/extensions/local-security-test.host-terminal-probe-0.0.1`から読み込みました。通常の接続で実行し、成功した試行では`--extensionDevelopmentPath`を使っていません。
- 拡張機能は一度だけ動作し、`workbench.action.terminal.newLocal`を呼びました。新しいターミナルがactiveになったことを確認してから、`workbench.action.terminal.sendSequence`へPythonコマンドと改行を渡しました。ホスト側のシェルは`bash --noprofile --norc`です。
- コマンドの処理は、専用の`/tmp/vscode-host-terminal-probe-5cba7a2b2ce2/host-result.json`へランダムな識別子と実行環境を書き出すだけです。このディレクトリはコンテナへマウントしていません。

### 結果

| 観測点 | 結果 |
| --- | --- |
| リモート拡張機能の実行場所 | hostname `2ff361e5c7d4`、UID 1000、`/.dockerenv`あり |
| 拡張機能による操作 | `new-local-terminal`、`sequence-sent`を順に記録 |
| ホストに作られたファイル | hostname `nixos`、UID 1000、cwd `/home/hogeyama`、`/.dockerenv`なし |
| ファイルに記録されたmount namespace | `mnt:[4026531832]`。ホストから別途読んだ値と一致 |
| コンテナのmount namespace | `mnt:[4026533492]`。ホストと異なる |
| ランダムな識別子 | 拡張機能のコマンドに埋め込んだ値とファイル内の値が一致 |
| コンテナから同じパスを確認 | ファイルは存在しない |

共有ファイルへの直接書き込みではなく、コンテナ内の拡張機能からVS Codeを通じてホストのユーザー権限でPythonを実行できました。コマンド送信後に、人間がターミナルでEnterを押す操作は行っていません。

この試行で確認したのは、`node`権限で配置した検証用拡張機能からの実行です。既存の第三者拡張機能の書き換え、Restricted Modeの回避、Windows上での実行は試していません。

## ローカルブラウザからcode-serverを使う境界検証

2026-10-15（JST）に、当時のbrowser-ideデモをLinuxのheadless Chromiumで検証しました。noVNCは使わず、ブラウザをbubblewrapのnetwork/mount/PID namespace内で起動し、固定宛先のUnix socket中継経由でmitmproxyに接続しています。試作コードと証跡ファイルは削除済みで、Git履歴に残っています。以下は当時の検証記録です。

| 観測点 | 結果 |
| --- | --- |
| Markdownプレビュー・統合ターミナル | 表示・コンテナ内のNode実行に成功 |
| リモート拡張からの`newLocal` | コマンドが存在せず失敗 |
| ホストだけに作った検証ファイルの読み取り | ENOENT |
| フォーム、fetch、beacon、画像、別タブ、リダイレクト、webviewからの通信 | receiver許可時9件、拒否時0件 |
| ブラウザとホストのnetwork namespace | 異なる |
| 直接TCP、UDP DNS、IPv6 UDPの検証用宛先への接続・送信 | 失敗 |
| 隔離内の`xdg-open` | 拒否用プログラムが終了コード126を返す |
| Chromium自身のsandbox | namespaceとSeccomp-BPFが有効 |

これはLinux側の上記経路についての動作試験です。外部プロトコル全種類やブラウザの脆弱性を網羅した検証ではありません。このheadless試験自体は日本語IMEの検証を含みません。Windowsで別途行った検証は次節に記録しています。元のデスクトップVS Codeの実験結果が無効になったこととは扱いません。

## Windows標準機能でのネイティブブラウザ検証

2026-10-15（JST）に、Windows 11 Home（10.0.26200）＋NixOS on WSLで、
当時の専用WebView2ブラウザをAppContainer内で動かしました。
Sandboxie、追加カーネルドライバ、noVNCは使用していません。Windows IMEについては
利用者から候補位置・インライン変換・再変換が「普段どおり動く」との実操作結果を得ています。

- WebView2 runtimeは155.0.4283.45、SDKは1.0.4258.31です。
- ブラウザ本体・GPU・ネットワーク・renderer等の子プロセスで同一のAppContainer SIDを確認しました。
- ネットワークcapabilityは付けず、同SIDのloopback例外と四つの永続WFP遮断ルールを設定しました。
  起動時にSIDと遮断条件を照合し、proxyのTCP `127.0.0.1:18081`だけを通します。
- 通常実行では接続できるTCP/UDP待受に対し、隔離内からはproxy以外の受信が0件でした。
  UDPの送信APIは成功を返す場合があるため、受信側の記録も検査しています。
- ホストだけの無害な検証ファイルの読み取り・書き込みは`UnauthorizedAccessException`でした。
- 一時登録した外部プロトコルは通常実行でホスト側マーカーを作成し、隔離内からの試行では作成されませんでした。
  ブラウザ自身でも外部スキームへのナビゲーションを取り消します。全プロトコルを網羅した結果ではありません。
- プレビューページのフォームPOST・fetch・beacon・画像・別ウィンドウ・リダイレクトは、
  receiver許可時11件（faviconを含む）、拒否時0件でした。フォームのPOST本文も検査しました。
- 実際のリモート拡張webviewのPOSTは許可時1件、拒否時0件でした。
  Windows側ブラウザでMarkdownプレビューと統合ターミナルのNode実行も確認しました。
- 最初の試作は96 DPI扱いで拡大表示がぼやけました。Per-Monitor V2に修正し、144 DPIを記録しました。

試作コードとブラウザ試験・OS境界試験の証跡ファイルは削除済みで、Git履歴に残っています。
これは実測した経路のデモであり、全Windowsサービスの代理通信やCOMインターフェース、
ブラウザ・OSの脆弱性を網羅する保証ではありません。元のデスクトップVS Codeでの
ホスト実行結果を取り消すものでもありません。DebianのFcitx5／IBusによるGUI実機検証は未完了です。

## LinuxのVS CodeクライアントからClaude Codeを使う

2026-10-15（JST）に、[隔離エディタ](../.devcontainer/claude/code-server/README.md)のLinuxクライアントを実機で確認しました。
ホストはDebian 12、Hyprland（Wayland）、Fcitx5、VS Code 1.119.0（commit `8b640eef`、nix）、bubblewrap 0.11.2です。
agentはclone modeで、DinD・nix・sumi・codeServer（`extensions = ["anthropic.claude-code"]`）を有効にしています。

### 起動

- `code-server.sh open`は、ホストの`code --version`に合う公式VS Code serverを取得して`vscode-server` volumeへ配備し、
  agentで8089番に起動したあと、bubblewrapの中でVS Codeを開きました。2回目以降は取得と配備を省き、起動だけで済みました。
- 最初の実装では接続が「the handshake timed out」で失敗しました。VS Codeのmanaged接続は
  `GET ws://localhost/...`の絶対形式のupgrade要求を送り、`skipWebSocketFrames=true`で101のあとをフレームなしに話すためです。
  resolver拡張が要求を`http://vscode.localhost:8000/...`へのproxy要求に書き換え、mitmproxyを`websocket=false`にして解決しました。
- サンドボックス内のVS Codeのログには、Marketplaceとupdate CDNへの「Offline」だけが残ります。外向きの経路がないためで、想定どおりです。

### agent内のClaude Codeで確認したこと

VS Codeの拡張ビューでagent側にanthropic.claude-codeがあり、Claude Codeに次を実行させて結果を得ました。

| 観測点 | 結果 |
| --- | --- |
| 実行場所 | hostname `a711d79f8779`、`node`、`/workspace`、`/.dockerenv`あり |
| 通信 | `https://example.com`はproxyがCONNECTに403、`https://api.anthropic.com`はCONNECTに200 |
| 編集と受け渡し | README.mdへの1行追加をagentの`main`にコミット。ホストの`git fetch agent`で取得できた |
| ビルドとテスト | `pnpm --dir frontend test`が1件合格。testcontainersがDinDの`busybox:1.37`をpullなしで起動 |
| 隔離 | `/home/cq2n-iwym`、`/home/node/.ssh`、`/var/run/docker.sock`はいずれも存在しない |

`mount`の出力には、node_modulesのoverlayの設定にあるホストのパス文字列が3行含まれます。中身は読めません。これは今回の変更とは無関係の既存の挙動です。

### 利用者の実操作で確認したこと

- 日本語IME（Fcitx5、text-input-v3経由）は、エディタと統合ターミナルで普段どおりに使えました。
- agent内の3000番で起動したHTTPサーバーは、ポートビューからSimple Browserで開けました。
  「ブラウザで開く」は何も起きません。サンドボックス内の`xdg-open`が拒否するためで、拒否のメッセージは画面には出ません。

### 未解決

- 最初の起動ではClaude Codeの認証に失敗しました。原因は特定できていません。`devcontainer up --remove-existing-container`と
  `inject-credentials.py`の実行後は通っています。proxyのコンテナIDは前後で同じで、認証情報の注入の記録もあるため、
  proxyに認証情報がなかったという説明は当てはまりません。再発したらproxyのログで拒否された要求を確認します。

## Electronシェルとweb版VS Codeの組み合わせ

2026-10-15（JST）に、[隔離エディタ](../.devcontainer/claude/code-server/README.md)のElectronシェルを、Debian 12のホストで動かすClaude Codeのサンドボックスの中で確認しました。
シェルはElectron 44.7.2で、headless（`--ozone-platform=headless`）で起動しました。
このサンドボックスは、Dockerのソケットへの接続とUnixソケットの作成を許しません。
そのため、agentのコンテナ、firewallの転送、Linuxクライアントのbubblewrapは含みません。
Electronの多重起動防止はUnixソケットを使うので、そこだけを無効にしたうえでシェルを読み込みました。

### シェルの判定

`tests/electron_shell_test.py`は、偽のIDEページからあらゆる経路で外へ出ようとし、記録用のproxyとホストのループバックの待受に何が届くかを調べます。

| 試行 | 結果 |
| --- | --- |
| 外部（example.com）、127.0.0.1、`app.localhost`、httpsへのfetch | すべて失敗。proxyにも届かない |
| 外部と127.0.0.1へのWebSocket | 失敗。agentの`localhost:3000`へのWebSocketは、proxyへのCONNECTになる |
| Web WorkerとService Workerからのfetch | 外部は失敗、`localhost:3000`はproxy経由で200 |
| 外部と127.0.0.1へのiframe、`data:`のiframeからのfetch、`<link rel=prefetch>`、`dns-prefetch`、`preconnect` | proxyにもループバックにも届かない |
| サーバーから外部へのリダイレクト | リダイレクト先への要求は出ない |
| `window.open` | `null`。要求も出ない |
| 外部プロトコルとトップレベルの外部への遷移 | どちらも拒否され、ページはそのまま次の処理へ進んだ。`xdg-open`などの代わりに置いたスクリプトは起動しない |
| ダウンロード | 保存されない |
| クリップボードの読み取り、通知、位置情報 | すべて拒否 |
| WebRTC（STUNサーバーを指定） | ICE候補は0件 |
| ホストのループバックの待受 | 接続は0件 |

最初の試行では、シェルの起動時に`redirector.gvt1.com:443`へのCONNECTがproxyまで届きました。
持続的なsession partitionを作ると、Electronがスペルチェックの辞書をすぐにダウンロードし始めるためです。
`setSpellCheckerEnabled(false)`ではこのダウンロードは止まりません。
sessionの作成時に言語の一覧を空にしてからは、起動から終了までproxyに届いた要求は`localhost`宛てだけになりました。

### web版VS Codeを通しで動かす

VS Code 1.142.0（commit `6d62b1d5`）のweb版serverを`agent/vscode-patches.py`で書き換え、ホストの127.0.0.1:18000で直接起動しました。
`code serve-web`は、serverとの間でUnixソケットを使うため、このサンドボックスでは起動できませんでした。
proxyには、code-server-proxyのベースimageから取り出したmitmproxy 11.1.3を`proxy.py`とともにbubblewrapで動かしました。
agent内のWebアプリの代わりに、ホストの3000番で、`SameSite=Lax`のCookieを設定し、`X-Frame-Options: SAMEORIGIN`とCSPの`frame-ancestors 'self'`を返すサーバーを動かしました。
workspace側の拡張機能として、webviewを開いてからSimple Browserで`http://localhost:3000/login`を開く検証用の拡張機能を入れました。

| 観測点 | 結果 |
| --- | --- |
| IDEの読み込み | 接続トークンがCookieになり、管理用と拡張機能ホスト用のWebSocketがどちらもつながった |
| 拡張機能のwebview | `localhost:18001`から読み込まれ、スクリプトの`fetch`がproxy経由でアプリに届き、拡張機能へのメッセージも届いた |
| Simple Browserでのログイン | `/login`が設定した`SameSite=Lax`のCookieが、続く`/check`に付いて送られた |
| iframeでの表示 | `/check`のページが描画され、ページ内の画像を要求した。`X-Frame-Options`とCSPを取り除かないproxyでは、`/check`は要求されたが描画されなかった |
| proxyが拒否した要求 | 0件 |

確認の途中で、webviewの次の問題が見つかり、`agent/vscode-patches.py`で直しました。

- webviewの`index.html`は、自分のホスト名が親のオリジンのハッシュでないと起動しない。`localhost:18001`では起動しなかった。
- IDEのページのCSPは、`frame-src`を`*.vscode-cdn.net`に限っていた。
- webviewはService Workerをmoduleとして登録する。module形式のService Workerのスクリプトは、仕様上Cookieなしで取得されるので、接続トークンがなく403になった。classic形式で登録すると、Cookieが付いて200になった。スクリプトにmodule構文はない。その後、シェルがserverへのすべての要求にトークンのCookieを付けるようにして、この書き換えはやめた。module形式のままで、webviewが表示されることを確かめた。

### Dockerでの確認

同じ日に、利用者のホストでagentのimage（x64、`INSTALL_VSCODE=1`、拡張機能なし）とproxyのimageをビルドし、`tests/code_server_integration_test.py`を実行して通りました。
このテストは、firewallと`route_localnet=1`を設定したagentと、proxyのコンテナを組み合わせて、次を確かめます。

- コンテナ内の`code serve-web`が、外部に接続できない状態でimageのserverを起動し、`/version`がimageのcommitを返す。
- 接続トークンがCookieになり、Cookieのない要求は403になる。IDEのページのCSPと`workbench.js`のwebviewのエンドポイント、webviewの`index.html`は、書き換えたとおりになっている。IDEへのWebSocketは101になる。
- proxyからの接続は、agentの127.0.0.1で待ち受けるアプリにも、0.0.0.0で待ち受けるアプリにも届く。アプリの応答から`X-Frame-Options`とCSPの`frame-ancestors`が外れ、外部へのリダイレクトは451になる。
- 外部、127.0.0.1、agentのアドレス、以前の`ide.localhost`や`port-3000.localhost`の名前への要求は451になる。
- 同じネットワークのほかのコンテナからは、agentの18000番にもアプリのポートにも接続できない。

続けて、このリポジトリの設定（`codeServer.extensions = ["anthropic.claude-code"]`、todeも有効）で`devcontainer build`を実行しました。
serverのCLIがMarketplaceからanthropic.claude-code 2.1.300を`/opt/vscode/extensions`に入れ、インストール後の確認も通って、ビルドは成功しました。

### 未確認

- arm64のimageのビルド。
- `devcontainer up`で作ったagentでの起動と、入れた拡張機能がエディタに読み込まれること。
- Linuxクライアント：bubblewrapの中でのElectronの起動（Chromiumのサンドボックスの入れ子）、Waylandでのウィンドウ表示、日本語IME。中継にUnixソケットを使うため、`tests/vscode_client_test.py`のbubblewrapの2件もこのサンドボックスでは失敗し、ほかの8件は通った。
- Windows：シェルの起動、外部プロトコル、WebRTC、IME。
- ユーザーの操作が要るもの：キーボードでの貼り付け、WebHIDなどの選択画面。
- Forgejoそのものでのログイン。上の確認は代わりのサーバーで行った。

## Linuxで隔離エディタを開く

2026-10-15（JST）に、利用者のホスト（Debian 12、Hyprland、Fcitx5）で`devcontainer up`と`code-server.sh open`を実行しました。

- `open`はElectron 44.7.2を取得してSHA-256を照合し、bubblewrapの中でシェルを起動しました。IDEが開き、統合ターミナルから`claude -p ping`が通りました。
- Claude Code拡張は、最初はグレーアウトしていました。Workspace Trustの制限モードで、未信頼のworkspaceに対応しない拡張機能が無効になっていたためです。手元の検証環境でも同じ表示を再現し、信頼すると拡張機能が起動することを確かめました。この確認を受けて、Workspace Trustを無効にしました。
- 信頼した後も、Claude Codeのパネルは空でした。シェルのログには、webviewの`index.html`（`localhost:18001`）の読み込みが`ERR_BLOCKED_BY_RESPONSE`で失敗した記録がありました。serve-webのCLIは、すべての応答に`X-Frame-Options`とCSPの`frame-ancestors 'self'`を付けます。どちらもポートを区別するので、`localhost:18000`のIDEは`localhost:18001`のwebviewをiframeに入れられません。手元の検証環境はserverを直接動かしていたので、この問題が出ませんでした。proxyに同じヘッダーを付けるスクリプトを加えて再現し、18001番の応答を書き換えたproxyでClaude Codeのパネルが表示されることを確かめました。
- 日本語IMEは、エディタとCtrl+Pの入力欄では使えましたが、統合ターミナルでは使えませんでした。IMEの窓は出るのに、確定すると文字が消えていました。Waylandのやり取りを記録すると、fcitx5は確定した文字をChromiumに渡していました。原因はagentのロケールでした。agentにはLANGがなく、VS Codeはターミナルに表示言語から`en_US.UTF-8`を設定しますが、imageにあるロケールは`C.UTF-8`だけです。BashはCロケールで動き、確定したマルチバイト文字を捨てていました。ターミナルで`export LANG=C.UTF-8`とすると入力できたので、imageで`LANG=C.UTF-8`を設定しました。
- VS Codeのポートビューは表示されず、ポートの自動転送も起きませんでした。serve-webのworkbenchは、起動オプションでtunnel providerを渡さないためです。各ポートをそれ自身に対応させるtunnel providerを加えるとポートビューが戻ることは確かめましたが、アプリは転送しなくても`http://localhost:<ポート>/`で開けるので、加えないことにしました。
- エディタやターミナルのlocalhostのリンクは、クリックしても何も起きませんでした。シェルが新しいウィンドウを拒否するためです。Machine設定に`"workbench.externalUriOpeners": {"http://localhost:*": "simpleBrowser.open"}`を置くと、手元の検証環境で、エディタのリンクのCtrl+クリックがSimple Browserのタブで開き、ページが描画されました。設定がなければ何も起きませんでした。拡張機能の`vscode.env.openExternal`はこの設定を使わないので、拡張機能が開くリンクには効きません。
- Win+Shift+Qでウィンドウを閉じられませんでした。web版VS Codeは、閉じるときに修飾キーが押されていると`beforeunload`で閉じるのを止め（`window.confirmBeforeClose`の既定値`keyboardOnly`）、Electronはそのときウィンドウを黙って開いたままにします。シェルがこの引き留めを無視するようにし、手元の検証環境でCtrlを押したまま閉じてもElectronが終了することを確かめました。直前の編集は自動保存でファイルに書かれていました。

## code-serverへの移行の検証（2026-10-15）

隔離エディタを`code serve-web`からCoder code-server 4.132.0へ移行した。
以前のserve-webと18001番に関する検証記録は、移行前の構成の記録として残す。

- amd64で、`INSTALL_TODE`と`INSTALL_CODE_SERVER`の4通りすべてをビルドし、必要なときだけcode-serverとtodeが入ることを確認した。todeの画面動作はこの検証の対象外。
- `CODE_SERVER_EXTENSIONS=anthropic.claude-code@2.1.300`でMarketplaceからのインストールと、インストール後のバージョン照合を確認した。
- `tests/code_server_integration_test.py`は、新しいagentとproxyのimageで成功した。実際のfirewall、外部接続なしでの起動、64桁トークン、Cookie認証、WebSocket、同一オリジンのwebviewリソース、アプリの転送、`/proxy/3000/`の302を検証する。
- `tests/electron_shell_test.py`はElectron 44.2.0で成功した。Cookieは18000番の要求に注入され、シェルから外部やホストのループバックへは到達しない。
- `tests/code_server_browser_test.py`は、実際のcode-server、変更後のmitmproxy、headlessのElectron 44.2.0で成功した。シェルは本番の`main.js`を読み込み、Chromiumのsandboxを有効にしたまま動かした。テスト用のブラウザ側コードで画面を観察し、ポートビューへ3000番を入力した。

画面テストで確認する内容は次のとおり。

| 確認対象 | 確認方法 |
| --- | --- |
| ワークベンチの接続 | リモートの検証用拡張が起動して結果を書く |
| webview | 同一オリジンのwebview内のスクリプトから拡張ホストへメッセージが届く |
| Simple Browser | `localhost:3000`のページと、絶対パス`/asset.js`が実行される |
| 変換後のURL | `asExternalUri`が返す`localhost:18000/proxy/3000/`を開き、302を経由して`localhost:3000`に戻り、ページとJavaScriptが表示・実行される |
| ポートビュー | 3000番の行と、転送先`http://localhost:18000/proxy/3000/`が表示される |

画面テストはLinuxでローカルの18000番と3000番を使い、proxyだけを一時Dockerコンテナで起動する。稼働中のagentは変更しない。単独で再実行する場合は、次のように配布物の実行ファイルを指定する。

```sh
CODE_SERVER_BROWSER_BINARY=/path/to/code-server/bin/code-server \
ELECTRON_SHELL_TEST_ELECTRON=/path/to/electron \
CODE_SERVER_PROXY_TEST_IMAGE=claude-code-server-proxy:test \
python3 tests/code_server_browser_test.py
```

全体のPythonテストは239件中39件がスキップされ、4件が失敗した。失敗は、Nix環境でシェルが追加する環境変数に関する`inject_credentials_test.py`の3件と、bubblewrapがない環境でのエラー順序に関する`vscode_client_test.py`の1件。同じ4件は変更前のHEADから取り出したコードでも再現した。Windowsの画面とarm64の実行は今回検証していない。

同日の実環境への反映では、`code-server.sh`と`wait-ready.sh`に旧serverの`/version`チェックが残っていることが分かり、両方を`/healthz`へ変更した。`devcontainer up --workspace-folder . --remove-existing-container`の正常終了を確認し、WindowsのElectronウィンドウを開いた。タイトルは`Welcome - workspace - code-server [agent]`で、server側でもManagementConnectionとExtensionHostConnectionの確立を確認した。Windowsでのwebviewやアプリ表示の手動確認は、この起動確認には含まない。

その後、認証情報の読み込みでシェル起動時の環境変数を除外するよう修正し、Linuxクライアントでは起動トークンを依存ツールより先に検査するよう変更した。回帰テストを3件追加し、全体のPythonテストは242件、失敗なし、39件スキップで完了した。また、WindowsのTerminalでCtrl-VがClipboard APIの読み取り拒否により失敗するという利用者の報告を受け、READMEの「キーボードでの貼り付けは動く」という説明を訂正した。クリップボードの権限は変更していない。
