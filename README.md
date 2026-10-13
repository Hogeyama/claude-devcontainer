# Claude Code用Dev Container

Claude Code用のDev Containerです。危険な操作を禁止しつつ、DinDなどの機能をエージェントに提供します。

リポジトリにgit subtreeで取り込んで使うことを想定しています。

## ホストに必要なもの

- Linux x86_64
- Docker EngineとDocker Compose、およびDockerを操作できる権限
- Dev Containers CLI（`devcontainer`コマンド）
- git subtree（多くのディストリビューションのgitに同梱されています）
- Python 3.11以上
- privilegedコンテナを実行できる権限（DinDを使う場合）
- Nix（NixのDev Shellを使う場合）

## 利用上の制限・注意

* GitHubを使う場合は、共有するリポジトリだけを対象にしたfine-grained tokenを使う前提です。
* agentが接続できるのは、Anthropicと、`.devcontainer/claude.toml`の`proxy.hosts`に書いた接続先だけです。package registryなども、`proxy.hosts`に書かなければ使えません。
* agentが動いているコンテナにVS Codeを接続すると、この隔離は成り立ちません。[VS Codeで開く](docs/usage.md#vs-codeで開く)を参照してください。
* Anthropicのサーバーから外部へ接続する機能は既定で拒否します。WebSearchは[`proxy.web_search = true`](docs/network-policy.md#websearchを使う)で、claude.aiのコネクタは指定したものだけ使えます。
  * [通信ルールと隔離の範囲](docs/network-policy.md)に対応範囲を記載しています。
* 既定の`git.isolation = "clone"`では、起動時にコンテナ内リポジトリを指す`agent`リモートをホストへ自動登録します。受け渡しはホストから直接fetch/pushし、GitHubなどの外部リポジトリは介在しません。初回起動はホストのローカルHEADからコピーするため、未pushでも起動できます。未コミット・未追跡ファイルはagentへ渡りません。
* 既定ではagentは専用volumeのcloneで作業します。ホストの作業ツリーと`.git`は共有しません。成果はホストの`git fetch agent`で受け取り、[コミットIDを固定してレビュー・取り込み](docs/usage.md#ホストでレビューして取り込む)ます。
* `[git] isolation = "shared"`で未追跡・ignoredファイルも含め直接共有できます（[設定と切り替え](docs/usage.md#gitの共有と分離)）が、リスクを理解したうえで使う必要があります。

## クイックスタート

### 1. リポジトリに取り込む

使いたいリポジトリのルートで、このリポジトリの`dist`ブランチを`.devcontainer/claude`に取り込み、テンプレートを`.devcontainer/`へコピーしてください。取り込み先は`.devcontainer/claude`に固定です。

```sh
git subtree add --prefix=.devcontainer/claude https://github.com/Hogeyama/claude-devcontainer dist --squash
cp -r .devcontainer/claude/template/. .devcontainer/
git add .devcontainer && git commit -m "Add Claude Code dev container"
```

`dist`ブランチは、このリポジトリの`.devcontainer/claude`だけを切り出したものです。取り込むと次の構成になります。

```
.devcontainer/
├── devcontainer.json
├── compose.yaml        claude/compose.yamlをincludeしつつ、リポジトリのマウント設定等を追加する
├── agent-setup.sh      agentの動くイメージにツールや設定を追加する
├── claude.toml         DinDなどの機能、proxyが許可する接続先と認証、DinDに読み込むイメージの設定
├── claude.local.toml   そのホストだけの設定（任意、コミットしない）
├── .gitignore          claude.local.tomlをコミットしないようにする
└── claude/             取り込んだもの
```

### 2. 認証情報を用意する

#### Claude

ホストのClaude Codeでログインしておいてください。agentのClaudeは、ホストのログインを使います。

```sh
claude /login
```

API keyを使う場合は、[Claude ConsoleのAPI keys](https://platform.claude.com/settings/keys)で発行したkeyを、3.で`ANTHROPIC_API_KEY`に設定してください。ただし、API keyではArtifactsやclaude.aiのコネクタなどは使えません。

#### GitHub機能の認証（利用する場合）

ホストとコンテナ間の受け渡しには認証設定は不要です。GitHubのリポジトリやAPIへアクセスする場合は、GitHubで[fine-grained PATを発行](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-fine-grained-personal-access-token)してください。resource ownerを指定し、共有を許可した非公開リポジトリだけを対象にしてください。Contents、Issues、Pull requestsなど、作業に必要な権限だけを付けてください。

### 3. 認証情報の取り出し方を設定する

`claude.toml`の`proxy.hosts`の`inject`が参照する秘密（テンプレートでは`GITHUB_TOKEN`のみ）それぞれについて、
ホストの`~/.config/claude-devcontainer/credentials`に、認証情報を取り出すシェルの代入文を書いてください。
Claudeの認証にAPI keyを使う場合は追加で`ANTHROPIC_API_KEY`を設定します。

```sh
GITHUB_TOKEN=$(pass github/token/for-agent)                       # passを使う場合
# GITHUB_TOKEN=$(secret-tool lookup service github)               # libsecretを使う場合
# GITHUB_TOKEN=$(op read op://Private/github/token/for-agent)     # 1Passwordを使う場合 
# GITHUB_TOKEN=github_pat_...                                     # 値を直接書く場合。ファイルは0600にしてください

# ANTHROPIC_API_KEY=$(op read op://Private/anthropic/credential)  # API keyを使う場合のみ
```

> [!NOTE]
> 認証情報の注入スクリプトは、このファイルをホストのシェルで実行します。agentが書き換えられる作業ツリーの中には置かないでください。

### 4. 起動する

初回は、ホストのローカルHEADを専用volumeへ自動でコピーします。事前のpushは不要です。受け渡し用リモート`agent`の手動設定も不要です。[利用手順](docs/usage.md#受け渡し用リモート)を参照してください。既存の`origin`は変更しません。

#### CLIとして実行する場合

```sh
devcontainer up
devcontainer exec claude
```

Dev Containersがimageのビルドとコンテナ（本体とプロキシ他）の起動を行います。
起動時に3.で設定した認証情報がプロキシに読み込まれます。既定のcloneモードでは、空のworkspace volumeにホストのHEADと同じコミットを自動取得し、ホストと同じブランチ名でcheckoutします（ホストがdetached HEADなら同じくdetached HEAD）。必要ならコンテナ内で`git switch -c agent/task-name`して作業ブランチを作れます。再起動・再作成では既存cloneをfetch/resetせず、ブランチと未コミットの変更を保持します。

> [!NOTE]
> Claudeが接続できない場合は、[接続できない場合](docs/troubleshooting.md)を確認してください。

#### VS Codeを使う場合

agentが動いているコンテナには、VS Codeを接続しないでください。接続すると、コンテナで動くプロセスが、ホストでコマンドを実行したり、proxyを通らずに外部と通信したりできるようになります。agentの変更は、ホストのリポジトリへfetchし、ホストで差分を見てください。理由と、それでも接続する場合の手順は、[VS Codeで開く](docs/usage.md#vs-codeで開く)を参照してください。

コンテナの中でVS Codeを使う場合は、ターミナルで動かす[tode](docs/usage.md#ターミナルでvs-codeを使うtode)を使ってください。

### 5. 終了する

Claudeを終了するには、Ctrl-Cを押すか`/exit`を入力してください。VS Codeで開いていた場合は、ウィンドウを閉じるとComposeも停止します。

CLIで使っていた場合は、ホストで取り込んだリポジトリのルートで次を実行してComposeを停止してください。

```sh
.devcontainer/claude/scripts/compose.sh down
```

`compose.sh`は、このリポジトリのDev Containerを見つけて`docker compose`に引数を渡します。`logs proxy`のように`down`以外のサブコマンドも使えます。

作業ファイル、Claudeのセッション履歴（`~/.claude/projects`）、DinDのデータ、プロキシのCAは残ります。`down --volumes`はホストへ未取得のコミット・未コミットの作業も消します。通常の停止や依存更新には使わないでください。Node・GradleのCoWのupperはホストの状態ディレクトリに残ります。依存の更新方法は[依存の共有と更新](docs/usage.md#依存の共有と更新)を参照してください。

## カスタマイズする

`.devcontainer/claude/`は取り込んだものなので、直接編集しないでください。変更は次の3か所で行います。

- `.devcontainer/claude.toml`と`claude.local.toml`: 機能の有効化、接続先、DinDに読み込むイメージなど、このDev Container独自の設定
- `.devcontainer/`直下の`agent-setup.sh`、`compose.yaml`、`devcontainer.json`: Dev Containerの通常の設定
- ホストの`~/.config/claude-devcontainer/`と`~/.claude`: 秘密の値や、agentへコピーするホストの設定

| 変えたいもの | 書く場所 | 節 |
|---|---|---|
| agentに入れるツール | `agent-setup.sh` | [agentの環境を変える](#agentの環境を変える) |
| agentの環境変数・volume | `compose.yaml` | [agentの環境を変える](#agentの環境を変える) |
| VS Codeの拡張機能など | `devcontainer.json` | [agentの環境を変える](#agentの環境を変える) |
| 接続先と、proxyが付ける認証 | `claude.toml`の`proxy.hosts`、ホストの`credentials` | [接続先を変える](#接続先を変える) |
| 自分だけが使う接続先 | `claude.local.toml`の`proxy.hosts` | [接続先を変える](#接続先を変える) |
| 社内CAを使う接続先 | `claude.toml`の`proxy.ca` | [接続先を変える](#接続先を変える) |
| claude.aiのコネクタ | `claude.local.toml`の`proxy.connectors` | [接続先を変える](#接続先を変える) |
| Claude Codeの設定・hook・skillなど | ホストの`~/.claude`、`extra/` | [ホストの設定を引き継ぐ](#ホストの設定を引き継ぐ) |
| Docker（DinD） | `claude.toml`の`docker` | [Dockerを使う（DinD）](#dockerを使うdind) |
| nixのdev shell | `claude.toml`の`nix` | [nixのdev shellを使う](#nixのdev-shellを使う) |
| シークレットのマスク | `claude.toml`の`sumi`、ホストの`sumi/secrets.txt` | [シークレットをマスクする](#シークレットをマスクする) |
| agentのUID・ネットワーク | `claude.local.toml`の`agent`、`subnets` | [UIDとネットワークを変える](#uidとネットワークを変える) |

ホストの`credentials`、`extra/`、`sumi/`は、`~/.config/claude-devcontainer/`の下にあります。

### claude.tomlとclaude.local.toml

このDev Container独自の設定は、`.devcontainer/claude.toml`と`claude.local.toml`に[TOML](https://toml.io/ja/v1.0.0)で書きます。2つのファイルの形式と項目は同じで、使い分けは次のとおりです。

| ファイル | 書く設定 | Git |
|---|---|---|
| `claude.toml` | リポジトリで共有する設定 | コミットする |
| `claude.local.toml` | そのホストだけの設定 | コミットしない（テンプレートの`.gitignore`で除外済み） |

書ける項目と既定値は、テンプレートの`.devcontainer/claude/template/claude.toml`に載っています。書かなかった項目は既定値になります。どちらのファイルも`.devcontainer/`にあるので、agentからは書き換えられません。

`claude.local.toml`の値は`claude.toml`の値より優先します。テーブルは項目ごとに混ぜ、それ以外の値は丸ごと置き換えます。たとえば`claude.toml`でDinDを有効にしていても、`claude.local.toml`に次を書けば、そのホストでは無効になります。

```toml
[docker]
enabled = false
```

配列も置き換えです。`claude.local.toml`に`docker.images`を書くと、`claude.toml`の一覧には足されず、その一覧だけになります。`proxy.hosts`は、同じホストの設定を丸ごと置き換えます。

設定を変えたら、[終了する](#5-終了する)の手順で環境を停止し、`devcontainer up`で起動し直してください。`agent`、`docker.enabled`、`nix.enabled`、`sumi.enabled`、`tode.enabled`、`tode.extensions`を変えたときは、リポジトリのルートで次を実行してコンテナも作り直してください。

```sh
devcontainer up --remove-existing-container
```

`devcontainer up`は、起動の前に2つのファイルを検査します。知らない項目や型の違う値があると、ファイル名と項目名を示して止まります。

設定はこの2つのファイルからだけ読みます。`CLAUDE_DEVCONTAINER_DIND`などの環境変数や`.devcontainer/.env`で指定することはできず、指定されていると`up`がエラーにします。

起動処理は、2つのファイルの内容からComposeとproxyが読むファイルを作り、`.devcontainer/.generated/`に置きます。このディレクトリは編集しないでください。

### agentの環境を変える

- ツールは、導入手順を`agent-setup.sh`に書きます。このスクリプトはagentのimageをビルドするときにホストでrootとして実行されるので、package registryにも接続できます。
- 環境変数やvolumeは、`compose.yaml`の`services.agent`に、Composeの通常の書き方で追加します。
  - proxyとDinDのアドレスは、`claude-proxy`と`claude-dind`という名前で書けます。たとえばJavaは`HTTPS_PROXY`を読まないので、`JAVA_TOOL_OPTIONS: -Dhttps.proxyHost=claude-proxy -Dhttps.proxyPort=8080`のように指定します。
- VS Codeの拡張機能などは、`devcontainer.json`に書きます。

> [!WARNING]
> `compose.yaml`では`cap_add`、`privileged`、`networks`なども上書きできてしまいます。agentの隔離はこれらの設定に依存しているので、変更しないでください。

### 接続先を変える

proxyが通す接続先は、`claude.toml`の`proxy.hosts`にホストごとに書きます。通す要求と、proxyが付ける認証をここで決めます。認証に使う秘密の値は、ホストの`~/.config/claude-devcontainer/credentials`に書きます。書き方は[接続先を追加する](docs/network-policy.md#接続先を追加する)を参照してください。

```toml
[proxy.hosts."api.notion.com"]
routes = ["/v1/.+"]
inject = { Authorization = "Bearer ${NOTION_API_KEY}" }
env = ["NOTION_API_KEY"]
```

- 自分だけが使う接続先（Redmineなど）は、`claude.local.toml`の`proxy.hosts`に書きます。詳しくは[ホストだけの接続先を追加する](docs/network-policy.md#ホストだけの接続先を追加する)を参照してください。
- 社内CAの証明書を使う接続先がある場合は、CA証明書のパスを`proxy.ca`に書きます。詳しくは[社内CAを信頼させる](docs/network-policy.md#社内caを信頼させる)を参照してください。
- claude.aiのコネクタは、使うもののIDを`claude.local.toml`の`proxy.connectors`に書きます。IDはアカウントごとに違います。詳しくは[claude.aiのコネクタを使う](docs/network-policy.md#claudeaiのコネクタを使う)を参照してください。

### ホストの設定を引き継ぐ

`devcontainer up`のたびに、ホストの`~/.claude`から次のものをagentへコピーします。`.credentials.json`、セッション履歴、`plugins/`などはコピーしません。

- `settings.json`、`CLAUDE.md`、`skills/`、`agents/`、`commands/`、`output-styles/`
- `settings.json`の`statusLine`と`hooks`のコマンドが`~/.claude/…`（`$HOME/.claude/…`でも可）で参照するファイル

`settings.json`はmanaged settings（`/etc/claude-code/managed-settings.d/50-host.json`）として置き、ほかのファイルはroot所有でコピーします。そのため、agentはホストで設定したhookを外したり、hookが実行するスクリプトを差し替えたりできません。

これ以外のファイルを渡したいときは、`~/.config/claude-devcontainer/extra/`に、ホームディレクトリと同じ構成で置いてください。agentのホームディレクトリへコピーします。symlinkでも構いません。`extra/.claude/settings.json`を置くと、ホストの`settings.json`の代わりにそれを使います。

```sh
cd ~/.config/claude-devcontainer/extra
mkdir -p .claude .config/git
ln -s ~/.claude/statusline.sh .claude/statusline.sh
ln -s ~/.config/git/config .config/git/config
```

Gitの設定は自動ではコピーしません。credential helper、署名、deltaのようなpagerなど、コンテナでは動かない設定が混ざりやすいためです。ホストの設定をそのまま渡すか、コンテナ用に書いたファイルを`extra/`に置いてください。symlinkで`~/.claude`ごと渡しても、`.credentials.json`はコピーしません。

> [!NOTE]
> - コピーはホストからコンテナへの一方向です。managed settingsは、コンテナ内で`/config`などから変えた設定やプロジェクトの設定より優先されます。コンテナ用の値にしたいときは`extra/.claude/settings.json`を使ってください。
> - agentの`~/.claude`はコンテナを再起動すると消えます（セッション履歴の`projects/`は残ります）。
> - コピーしたファイルはroot所有なので、自分と同じディレクトリにファイルを書くhookスクリプトは失敗します。
> - `settings.json`の`env`もagentから見えます。秘密を書いているなら、`extra/.claude/settings.json`にコンテナ用の設定を用意してください。
> - コマンドのパスは、`/home/<user>/…`のような絶対パスではなく`~`か`$HOME`で書いてください。スクリプトが使うツールは`agent-setup.sh`で入れてください。
> - `up`の後にホストで設定を変えたら、`.devcontainer/claude/scripts/copy-host-config.py`を実行すると反映されます。

### Dockerを使う（DinD）

`claude.toml`に次を書くと、agentからDocker（DinD、Docker in Docker）を使えます。

```toml
[docker]
enabled = true
images = ["postgres:16", "testcontainers/ryuk:0.14.0"]
```

DinDは外部のregistryに接続できません。使うイメージは`images`に書いてください。`devcontainer up`のたびに、ホストがイメージを用意してDinDへ読み込みます。読み込みの詳細やポートへの接続方法は、[DockerとTestcontainers](docs/usage.md#dockerとtestcontainers)を参照してください。

### nixのdev shellを使う

ホストにnixがあれば、リポジトリのflakeが定義するdev shellのツールを、agentのBashでも使えます。flakeをコミットしてから、`claude.toml`に次を書いてください。

```toml
[nix]
enabled = true
```

既定では、`HEAD`にあるflakeのdefault dev shellを使います。別のコミットやdev shellを使う場合は、次の項目を足します。

```toml
[nix]
enabled = true
ref = "HEAD"                            # 評価するコミットやref。既定はHEAD
shell = "devShells.x86_64-linux.foo"    # 名前付きdev shell。省略するとdefault
```

`devcontainer up`のたびに、ホストで`nix print-dev-env`を実行し、`nix develop`が読み込むスクリプトを書き出します。agentは起動時に1回だけ、このスクリプトを`shellHook`まで`/workspace`で実行し、その後でexportされている環境変数をBashで読み込みます。`nix develop -c claude`で起動したclaudeが受け取る環境と同じものです。ただし、`TZ`や`SOURCE_DATE_EPOCH`のようにビルドにしか使わない変数は除きます。bashの関数とエイリアスは引き継ぎません。ホストの`/nix/store`は読み取り専用で使います。

`shellHook`はagentの中でnodeとして実行するので、通信はproxyの制限を受けます。出力はagentの`/run/claude-devcontainer/nix-env.log`に残ります。`shellHook`が120秒以内に終わらない場合、agentのBashはdev shellの環境なしで動きます。コンテナが動いている間は、`devcontainer up`を実行し直しても`shellHook`は再実行されません。flakeの変更を反映するには、`devcontainer up --remove-existing-container`でコンテナを作り直してください。

> [!WARNING]
> * flakeの評価、inputの取得、ビルドはホストで実行するので、proxyの通信制限を受けません。ホストのコミットを評価し、agentのcloneのrefは使いません。flakeの変更もホストへ取り込む前にレビューしてください。
> * agentからはホストの`/nix/store`全体を読めます。store内の設定ファイルなどに秘密が含まれていれば、その値も見えます。

### シークレットをマスクする

[sumi](https://github.com/Hogeyama/nix-agent-sandbox/tree/main/contrib/sumi)を有効にすると、`Read`や`Bash`などのツールの出力に含まれる指定した値を、`*`に置き換えてからClaudeへ渡します。作業ツリーの`.env`や`application.properties`にあるパスワードなどを、Claudeに見せたくないときに使います。

まず、マスクしたい値の一覧を、ホストの作業ツリーの外に1行1件で作ります。既定の場所は`~/.config/claude-devcontainer/sumi/secrets.txt`です。権限は0600にし、所有者をagentのUID（既定は1000）に合わせてください。ホストのユーザーとUIDが違う場合は、[UIDとネットワークを変える](#uidとネットワークを変える)も確認してください。

```sh
mkdir -p ~/.config/claude-devcontainer/sumi
[ -e ~/.config/claude-devcontainer/sumi/secrets.txt ] || install -m 600 /dev/null ~/.config/claude-devcontainer/sumi/secrets.txt
${EDITOR:-vi} ~/.config/claude-devcontainer/sumi/secrets.txt
chmod 600 ~/.config/claude-devcontainer/sumi/secrets.txt
```

次に、`claude.toml`に次を書きます。sumiの起動とhookの設定は自動で行うので、ホストで`sumi init`を実行する必要はありません。

```toml
[sumi]
enabled = true
```

一覧を別の場所に置く場合は、`sumi.secrets`にパスを書きます。相対パスは`.devcontainer/`が基準で、`~`はホストのホームディレクトリです。場所がホストごとに違うなら、`claude.local.toml`に書いてください。

```toml
[sumi]
secrets = "/home/me/secrets/sumi.txt"
```

一覧は、ネットワークを持たない`sumi`コンテナにだけ、読み取り専用で共有します。agentには共有せず、ホストの設定のコピーからも除きます。ただし、`sumi`コンテナには一覧のあるディレクトリごと共有するので、同じディレクトリにほかの秘密を置かないでください。

一覧を編集すると、再起動しなくても次のツール実行から反映されます。実行中のコマンドには反映されません。空のファイルや不正な形式などで読み直せない場合は、それまでの一覧を使い続け、sumiのログに警告を出します。

> [!WARNING]
> sumiはツールの出力をマスクするだけで、作業ツリーのファイルをagentから隠すものではありません。`CLAUDE.md`のようにhookを通らずに読まれる内容や、二重のbase64など対応していない形式に変換された値は伏せられません。マスクの結果から値を推測されることも防げません。詳しくは[sumiのリミテーション](https://github.com/Hogeyama/nix-agent-sandbox/tree/main/contrib/sumi#リミテーション)を確認してください。

### UIDとネットワークを変える

agentのUIDと内部ネットワークは、ホストごとに違うことが多いので、`claude.local.toml`に書きます。書かなかった項目は既定値になります。

```toml
[agent]
uid = 1001             # agentのUID。既定は1000
gid = 1001             # agentのGID。既定は1000

[subnets]
proxy = "10.99.0"      # agentとproxyの間のネットワーク。既定は自動で選ぶ
dind = "10.99.1"       # agentとDinDの間のネットワーク。既定は自動で選ぶ
```

- UIDとGIDは、ホストでリポジトリを所有するユーザーに合わせてください。DinDのdockerdも同じUIDで動きます。0と、DinDが内部で使う100000〜165535は指定できません。
- ネットワークは`/24`の先頭3オクテットで書きます。proxyとDinDは`.10`、agentは`.20`を使います。書かなければ、既存のDockerネットワークと重ならない`10.231.x.0/24`を2つ選び、空いている限り次の起動でも同じ値を使います。同じホストで複数のリポジトリを同時に起動すると、同じ値を選ぶことがあります。その場合は、片方を起動し直すか、ここで値を指定してください。

UIDを変えると、workspace、DinD、Claudeの履歴、依存のupperに古い所有者が残ります。先にホストへ未取得のコミットと未コミットの作業を退避し、停止後に対象データの所有者を移行してください。`down --volumes`で消す場合は、workspaceの作業も失われます。

## 更新する

```sh
git subtree pull --prefix=.devcontainer/claude https://github.com/Hogeyama/claude-devcontainer dist --squash
devcontainer up --remove-existing-container
```

`.devcontainer/claude/template/`が変わった場合は、`.devcontainer/`直下の自分のファイルへ必要に応じて反映してください。

`.devcontainer/claude/`を直接編集した場合も、`git subtree pull`が上流の変更と3-way mergeするので、衝突しなければ手元の変更は残ります。ただし隔離にかかわる設定が上流の修正と混ざるため、できるだけ避けてください。

## このリポジトリを開発する

このリポジトリ自身も同じ構成で、`.devcontainer/claude`が利用者のリポジトリに取り込まれる部分、その外がこのリポジトリ用のファイルです。`claude.toml`でDinD・nix・sumiを有効にしています。`flake.nix`のdev shellにはPython、Node.js、pnpm、JDK 17、Gradle、Git、Docker CLI、OpenSSL、ShellCheck、playwright-cli（Chromium）が入り、ホストでは`nix develop`、agentではBashから使えます。

sumiのデモ用の公開値を`.secrets/sumi/secrets.txt`にコミットしています。これはcloneにも入ります。実際の秘密をGitへコミットしないでください。sumiの一覧の実ファイルは引き続きsumiサービスだけにmountします。旧構成でこのデモファイルに付けた`assume-unchanged`は不要です。

`.devcontainer/`直下のファイルは、取り込んだ後の状態を示す例です。たとえば`agent-setup.sh`はJDKを入れ、`compose.yaml`はホストの`~/.gradle`をagentの`~/.gradle`にcopy-on-writeでmountします。agentのGradleはホストで取得済みの配布物と依存でオフラインに動き、agentが書き込んだものは`~/.local/state/claude-devcontainer/gradle-home/upper`に残ってホストの`~/.gradle`には入りません。agentの起動中は、ホストでGradleを実行しないでください（overlayfsは、mount中に下の層が変わったときの動作を定めていません）。どれもこのリポジトリの開発には使いません。自分のリポジトリでは`.devcontainer/claude/template/`のテンプレートから始めてください。

依存の共有とDinDのデモとして、ルートに小さなアプリを置いています。

- `frontend/`: pnpmで管理するReact+Viteのフロントエンド。`frontend/node_modules`は`compose.yaml`が追加する2番目のoverlayでホストとCoW共有します（lowerdirは`prepare-deps.sh`が`.devcontainer/.env`に書き出す絶対パス）。vitestとtestcontainersのテストがあり、DinD上にbusyboxを起動してHTTPを検証します
- `backend/`: Javalinのバックエンド。Gradleはwrapper経由で、配布物と依存はホストの`~/.gradle`から供給します。`/api/message`がPostgresの値を返します。JUnitとTestcontainersのテストがあり、DinD上に`postgres:16-alpine`を起動します
- `compose.yaml`: 上の2つとPostgresを協調して起動するアプリ用のスタック。agentの中ではDinDで動き、`3000:80`のpublishがdind-bridgeでagentの`127.0.0.1:3000`に中継されます。ホストでもそのまま起動できます

依存の取得ができるのはホストだけなので、先にホストで用意します。

```sh
# ホスト（nix developの中）
pnpm -C frontend install
cd backend && ./gradlew build
```

agentの中では、共有された依存でテストと起動ができます。

```sh
pnpm -C frontend test                          # busyboxコンテナへのfetch
cd backend && ./gradlew --offline test; cd ..  # Testcontainers + postgres
pnpm -C frontend build && cd backend && ./gradlew --offline installDist && cd ..
docker compose up -d                           # DinD上でdb→backend→frontendの順に起動
playwright-cli open http://localhost:3000
playwright-cli screenshot --filename demo.png
```

agentに必要なイメージは`claude.toml`の`docker.images`に書いてあります。

`main`へpushすると、GitHub Actions（[`.github/workflows/dist.yml`](.github/workflows/dist.yml)）が[`scripts/build-dist.sh`](scripts/build-dist.sh)で`dist`ブランチにコミットを1つ足してpushします。中身は`main`の`.devcontainer/claude`と、それを最後に変更した`main`のコミットのURLを書いた`UPSTREAM`ファイルです。`.devcontainer/claude`にもそのコミットにも変化がなければ何もしません。`dist`は先へ足すだけで書き換えないので、`main`をforce pushしても利用者の`git subtree pull`は壊れません。ただし、force pushで消えた`main`のコミットを指す古い`UPSTREAM`のリンクは、いずれ見られなくなります。`dist`は手で変更しないでください。
