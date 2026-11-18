# hostexec

コンテナの中のエージェントが、ホストでのコマンドの実行を依頼するためのツールです。依頼は1回ごとに、ホストにいる人がブラウザか端末で承認します。承認されたコマンドはホストで、あなたの権限で実行され、出力は実行中から順にコンテナへ返ります。

[claude-devcontainer](https://github.com/Hogeyama/claude-devcontainer)のproxyを経由して使うことを想定しています。claude-devcontainerの設定（`proxy.hosts`、`inject`、`ca`、`agent.mounts`）だけで接続します。

```
[コンテナ]  hostexec -- nix build .#foo
     │ HTTPS_PROXY
     ▼
[proxy]  proxy.hosts."host.docker.internal": POST /run だけ通し、Authorization を付ける
     │ https://host.docker.internal:7878（hostexec の CA で検証）
     ▼
[ホスト]  hostexec serve ── 検証 → 承認待ち ──▶ http://127.0.0.1:7879（ブラウザ）/ hostexec review（端末）
                                  └ 承認 → 実行 → 出力を NDJSON で返す
```

設計の多くは[strait](https://github.com/Hogeyama/nix-agent-sandbox/tree/main/contrib/strait)の`hostexec`に倣っています。

## 必要なもの

- ホスト：Python 3.10以上（標準ライブラリだけを使います）、OpenSSL、Docker
- コンテナ：`python3`と`curl`（claude-devcontainerのagentにはあります）

## セットアップ

1. ホストで鍵とトークンを作ります。`~/.config/hostexec/`に置かれます。

   ```sh
   ./hostexec init
   ```

   `~/.config/claude-devcontainer/credentials.toml`に、`hostexec snippet --secrets`の出力を書きます（1回だけ）。

   ```toml
   [secrets]
   HOSTEXEC_TOKEN = { command = "cat ~/.config/hostexec/api-token" }
   HOSTEXEC_WORKSPACE = { command = 'printf %s "$CLAUDE_DEVCONTAINER_WORKSPACE"' }
   ```

   `HOSTEXEC_WORKSPACE`は、ワークスペースのホストでの絶対パスです。claude-devcontainerは、秘密のコマンドの環境変数`CLAUDE_DEVCONTAINER_WORKSPACE`にこれを入れます。

   次に、`hostexec snippet`の出力を、claude-devcontainerの設定に追記します。内容はワークスペースによりません。

   - `devcontainer up`（リポジトリモード）：使うワークスペースごとに、`.devcontainer/claude.local.toml`へ
   - `claude-devcontainer up`（ホストモード）：1回だけ、`~/.config/claude-devcontainer/claude.toml`へ

   ```sh
   ./hostexec snippet >> ~/repo/project/.devcontainer/claude.local.toml
   ```

   出力は次の内容です。

   ```toml
   [proxy]
   ca = "~/.config/hostexec/ca.pem"

   [proxy.hosts."host.docker.internal"]
   routes = ["POST /run", "GET /client"]
   https = 7878

   [proxy.hosts."host.docker.internal".inject]
   Authorization = "Bearer ${HOSTEXEC_TOKEN}"
   X-Hostexec-Map = "/workspace=${HOSTEXEC_WORKSPACE}"

   [agent.mounts]
   "/usr/bin/hostexec" = "~/path/to/hostexec/client/hostexec"
   ```

   書いたら、claude-devcontainerの環境を停止して起動し直してください。

2. ホストでサーバーを起動します。1つで、すべてのワークスペースからの依頼を受けます。

   ```sh
   ./hostexec serve
   ```

   承認用のURL（`http://127.0.0.1:7879/#<token>`）が表示されるので、ブラウザで開きます。端末で承認する場合は、別の端末で`./hostexec review`を実行します。URLは`./hostexec ui-url`でも表示できます。

   ログイン中ずっと動かしておく場合は、[systemdのユーザーサービス](#systemdで常駐させる)にできます。

3. コンテナでは、`snippet`の設定にある`agent.mounts`で、クライアントが`/usr/bin/hostexec`に読み取り専用でmountされます。

   ```sh
   hostexec -- uname -a
   ```

   mountを使わない場合は、APIから取得することもできます（`curl -fsS https://host.docker.internal:7878/client -o hostexec`）。

hostexecは、エージェントにこの機能の存在を知らせません。使わせる場合は、CLAUDE.mdなどに「コンテナ内で動かないコマンド（`nix build`など）は`hostexec -- <コマンド>`で実行する」のように書いてください。

## 使い方

```sh
hostexec [--cwd DIR] [--env NAME=VALUE | --env NAME]... [--] cmd [args...]
```

- 作業ディレクトリは`--cwd`で指定します。省略すると、コンテナでの現在のディレクトリです。proxyが付ける`X-Hostexec-Map`（`hostexec snippet`の設定）で、ワークスペースのホストのパスに変換されます。`serve`の`--map CONTAINER=HOST`も使えますが、同じコンテナのパスでは`X-Hostexec-Map`が優先します。最も長く一致する対応を使い、どれにも当たらないパスはそのまま使います。`..`は変換の前に解決します。
- 環境変数は、ホストの`PATH`と`HOME`に、`--env`で指定したものを加えたものだけです。`--env NAME`は、コンテナのシェルの値をそのまま渡します。
- stdinは使えません。出力は実行中から順に返ります。
- 終了コードはコマンドのものです。拒否・期限切れ・接続の失敗では126を返します。シグナルで終了した場合は128+シグナル番号です。
- 承認待ちは240秒で期限切れになり、拒否として扱われます。実行時間に上限はありません。
- クライアントを止める（Ctrl-Cなど）と接続が切れ、ホストのコマンドもプロセスグループごと止まります（SIGTERM、5秒後にSIGKILL）。`setsid`などで自分でグループを作った子プロセスは止まりません。

`HOSTEXEC_URL`で接続先を変えられます（既定は`https://host.docker.internal:7878`）。

### `hostexec serve`のオプション

| オプション | 説明 | 既定値 |
| --- | --- | --- |
| `--listen ADDR` | コンテナ向けのAPIの待ち受けアドレス | `172.17.0.1` |
| `--port N` | コンテナ向けのAPIのport | `7878` |
| `--ui-port N` | 承認用のUIのport（`127.0.0.1`だけで待ち受け） | `7879` |
| `--map CONTAINER=HOST` | 作業ディレクトリの変換。複数指定可。`X-Hostexec-Map`のない依頼や、それ以外のパスに使う | なし |
| `--no-notify` | 承認待ちを`notify-send`で知らせない | |

`--port`を変えた場合は、`hostexec snippet --port N`で設定を作り直し、コンテナの`HOSTEXEC_URL`も合わせてください。

Linuxでは、`host.docker.internal`はDockerのブリッジのホスト側のアドレス（通常`172.17.0.1`）を指します。ホストのファイアウォールがこのアドレスへの接続を止める場合は、許可してください。Docker Desktopでは、`--listen 127.0.0.1`にしてください。

### systemdで常駐させる

`hostexec service`は、`hostexec serve`を動かすsystemdのユーザーユニットを出力します。引数は`serve`のオプションで、そのままユニットの`ExecStart`に入ります。

```sh
mkdir -p ~/.config/systemd/user
./hostexec service > ~/.config/systemd/user/hostexec.service
systemctl --user daemon-reload
systemctl --user enable --now hostexec
```

- 承認したコマンドが受け取る`PATH`は、サーバーのものです。systemdのユーザーマネージャーの`PATH`はシェルのものと違うことが多いので、ユニットには`hostexec service`を実行したシェルの`PATH`を書きます。`PATH`を変えたら、ユニットを作り直してください。`HOME`はsystemdが設定します。
- 承認用のURLはjournalに出ます（`journalctl --user -u hostexec`）。`./hostexec ui-url`でも表示できます。
- 既定の待ち受けアドレス（`172.17.0.1`）はDockerのブリッジのものなので、Dockerより先に起動すると待ち受けに失敗します。ユニットは5秒ごとに起動し直します。
- 止めると（`systemctl --user stop hostexec`）、承認待ちの依頼は拒否され、実行中のコマンドも止まります。
- 承認待ちの通知（`notify-send`）には、ユーザーマネージャーにデスクトップのセッションバスが渡っている必要があります。届かなくても、ほかの動作には影響しません。

## 安全性

承認したコマンドは、ホストであなたと同じ権限で実行されます。コマンド、作業ディレクトリ、環境変数をすべて読んでから承認してください。Claude Codeのpermission promptは、この承認の代わりになりません。コンテナ内のどのプロセスも、promptを経ずに依頼を送れるためです。

- **APIのトークン**：コンテナ向けのAPIは、すべての要求に`Authorization: Bearer <api-token>`を求めます。トークンはproxyが`inject`で付けるもので、エージェントからは見えません。Linuxでは、APIの待ち受けアドレスに同じブリッジのほかのコンテナ（dindなど）からも届きます。トークンのない要求は401で拒否します。
- **TLS**：proxyは、`init`が作ったCAでhostexecの証明書を検証します。このCAは名前の制約で`host.docker.internal`だけに限られ、秘密鍵はサーバーの証明書に署名した直後に削除します。`ca`に加えても、ほかの名前の証明書は信頼されません。トークンはTLSの中だけを流れます（claude-devcontainerは、平文HTTPのホストへの`inject`を許しません）。
- **ワークスペースの対応**：`X-Hostexec-Map`はproxyが`inject`で付けるので、agentが送った同名のヘッダーは置き換えられます。agentは、自分の作業ディレクトリをほかのワークスペースのパスに変換させることはできません（`--cwd`にホストのパスを直接書くことはできるので、承認のときは作業ディレクトリも読んでください）。パスはヘッダーで届くため、印字可能なASCIIに限ります。それ以外のパスのワークスペースからの依頼は、400で拒否します。
- **proxyのroutes**：proxyで制限できるのは`POST /run`と`GET /client`までです。本文（argvなど）の検査と承認は、すべてhostexecが行います。本文は、未知のメンバー、重複したメンバー、NULを含む文字列やUTF-8で表せない文字列、不正な環境変数名を拒否します。
- **承認のUI**：`127.0.0.1`だけで待ち受けます。到達できること自体は権限になりません。APIの呼び出しには、UIのトークン（`ui-token`）、完全に一致する`Origin`と`Host`が必要です。このため、ほかのサイトやDNS rebindingからは承認できません。表示では、制御文字と書式文字（双方向テキストの制御など）をエスケープし、引数はシェルの語として表示します。`$'…'`の中のバックスラッシュは、必ずエスケープの開始です。
- **出力**：コマンドの出力にhostexecのトークンが含まれていれば、`[masked by hostexec]`に置換します。それ以外の出力はそのまま返ります。ホストの秘密を表示するコマンドを承認すれば、その秘密はコンテナに渡ります。
- **通知**：`notify-send`には固定の文言だけを送ります。コンテナから来た文字列は含めません。

`~/.config/hostexec/`をコンテナにmountしないでください。`api-token`があればproxyを経ずに依頼でき、`ui-token`があれば承認できます。

## 開発

```sh
python3 -m unittest discover -s tests -p '*_test.py'
```

`web/app.js`の表示がPython側（`hostexec review`）と同じかのテストには、`node`が必要です（ない場合は飛ばします）。

## ライセンス

MIT。strait（MIT）のコードを一部改変して使っています。
