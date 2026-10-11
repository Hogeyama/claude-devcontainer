# Claude Code用Dev Container

Claude Code用のDev Containerです。危険な操作を禁止しつつ、DinDなどの機能をエージェントに提供します。

自分のリポジトリにgit subtreeで取り込んで使ってください。取り込んだ部分（コア）は編集せず、ツールの追加などのカスタマイズはコアの外に置いた自分のファイルで行ってください。上流の修正は`git subtree pull`で取り込めます。

## ホストに必要なもの

- Linux x86_64
- Docker EngineとDocker Compose、およびDockerを操作できる権限
- Dev Containers CLI（`devcontainer`コマンド）
- git subtree（多くのディストリビューションのgitに同梱されています）
- Python 3
- DinDを使う場合は、privilegedコンテナを実行できる権限
- ホストのfirewallがコンテナ間通信を許可する環境
- nix（使う場合、Optional）

## 利用上の制限

* 安全に利用するために、読み取り権限のみのGitHub fine-grained tokenを利用することを前提にしています。
* package registry、GitHubのrelease assetやLFS、Anthropicのweb searchやMCP connectorなどは使えません。
  * [通信ルールと隔離の範囲](docs/network-policy.md)に対応範囲を記載しています。
* agentはリポジトリのファイルを1つのUID（既定は1000）で読み書きします。リポジトリの所有者と異なる場合は[UIDとネットワークを変える](#uidとネットワークを変える)で合わせてください。
* Gitリポジトリのルートで使ってください。`git worktree`の作業ツリーには対応していません。
* agentは`.devcontainer/`と`.git`の設定（config、hooks）を書き換えられません。ただし、agentが作ったコミットに含まれる`.devcontainer/`の変更は、ホストで反映する前に確認してください。詳しくは[ホストが実行する設定の保護](docs/network-policy.md#ホストが実行する設定の保護)を参照してください。

## クイックスタート

### 1. リポジトリに取り込む

使いたいリポジトリのルートで、コアを`.devcontainer/claude`に取り込み、雛形を`.devcontainer/`へコピーしてください。取り込み先は`.devcontainer/claude`に固定です。

```sh
git subtree add --prefix=.devcontainer/claude https://github.com/Hogeyama/claude-devcontainer dist --squash
cp .devcontainer/claude/template/* .devcontainer/
git add .devcontainer && git commit -m "Add Claude Code dev container"
```

`dist`ブランチは、このリポジトリの`.devcontainer/claude`だけを切り出したものです。取り込むと次の構成になります。

```
.devcontainer/
├── devcontainer.json   自分のファイル
├── compose.yaml        自分のファイル。claude/compose.yamlをincludeする
├── claude.env          自分のファイル。DinDなど、このリポジトリで使う機能の設定
├── agent-setup.sh      自分のファイル。agentにツールを追加する
├── images.txt          自分のファイル。DinDに読み込むイメージ
├── proxy.yaml          自分のファイル（任意）。proxyが通す接続先
└── claude/             取り込んだもの。編集しない
```

### 2. 認証情報を用意する

#### GitHub

GitHubで[fine-grained PATを発行](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-fine-grained-personal-access-token)してください。resource ownerを指定し、共有を許可した非公開リポジトリだけを対象にしてください。Contents、Issues、Pull requestsなど、作業に必要な権限だけを付けてください。

#### Claude

Claudeのサブスクリプションを使う場合は、ホストのClaude Codeで[`setup-token`](https://code.claude.com/docs/en/cli-reference)を実行し、対話手順に従って長期トークンを発行してください。

```sh
claude setup-token
```

API keyを使う場合は、代わりに[Claude ConsoleのAPI keys](https://platform.claude.com/settings/keys)で発行したkeyを使ってください。

### 3. 認証情報の取り出し方を設定する

ホストの`~/.config/claude-devcontainer/credentials`に、認証情報を取り出すシェルの代入を書いてください。`GITHUB_TOKEN`と、`CLAUDE_CODE_OAUTH_TOKEN`または`ANTHROPIC_API_KEY`のどちらか一方を設定してください。claude.aiのコネクタも使う場合は、代わりにホストのClaude Codeのログインを共有します（[ホストのClaude Codeのログインを共有する](docs/usage.md#ホストのclaude-codeのログインを共有する)）。取り出し方は手元の環境に合わせて選んでください。

```sh
GITHUB_TOKEN=$(pass github/token/for-agent)                       # passを使う場合
CLAUDE_CODE_OAUTH_TOKEN=$(secret-tool lookup service claude-code) # libsecretを使う場合
# ANTHROPIC_API_KEY=$(op read op://Private/anthropic/credential)  # 1Password CLIを使う場合
# GITHUB_TOKEN=github_pat_...                                     # 値を直接書く場合。ファイルはchmod 600にしてください
```

認証情報の注入スクリプトは、このファイルをホストのシェルで実行します。agentが書き換えられる作業ツリーの中には置かないでください。

### 4. 起動する

```sh
devcontainer up
```

Dev Containersがimageのビルドとコンテナ（本体とプロキシ。DinDを有効にした場合はDinDも）の起動を行います。
起動処理は、3.で設定した認証情報をプロキシへ自動で渡します。

### 5. 実行する

CLIとして実行する場合:

```sh
devcontainer exec claude
```

VS Codeで実行する場合:

1. ホストのVS CodeにDev Containers拡張をインストールしてください。
2. 取り込んだリポジトリで`code .`を実行し、通知またはコマンドパレットから「Dev Containers: Reopen in Container」を実行してください。VS Codeが上の手順で起動したコンテナにそのまま接続します。

VS Codeはホストの認証情報をコンテナへ転送する機能を持っています。使う前に[VS Codeで開く](docs/usage.md#vs-codeで開く)の利用条件を確認してください。

> [!NOTE]
> Claudeが接続できない場合は、[接続できない場合](docs/troubleshooting.md)を確認してください。

> [!NOTE]
> コマンドパレットから「Dev Containers: Install devcontainer CLI」を実行してCLIを落とすと、以降は `devcontainer open .` で直接 Dev Container を使った VS Code を開くことができます。
> npmやGitHubリリース経由でインストールできるdevcontainer CLIにはこの機能（`open`サブコマンド）がありません。

### 6. 終了する

Claudeを終了するには、Ctrl-Cを押すか`/exit`を入力してください。VS Codeで開いていた場合は、ウィンドウを閉じるとComposeも停止します。

CLIで使っていた場合は、ホストで取り込んだリポジトリのルートで次を実行してComposeを停止してください。

```sh
project=$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' \
  "$(docker ps -q --filter "label=devcontainer.local_folder=$(pwd -P)" --filter "label=com.docker.compose.service=agent")")
docker compose -p "$project" -f .devcontainer/compose.yaml down
```

作業ファイル、Claudeのセッション履歴（`~/.claude/projects`）、DinDのデータ、プロキシのCAは残ります。これらも消す場合は`down --volumes`を使ってください。

## カスタマイズする

コアの外にある自分のファイルを編集してください。

| 変えたいもの | 編集するファイル |
|---|---|
| agentのツール（言語処理系など） | `.devcontainer/agent-setup.sh`。imageのビルド時にrootで実行される |
| 環境変数・volumeなどcomposeの設定 | `.devcontainer/compose.yaml`の`services:`。相対パスは`.devcontainer/`が基準 |
| VS Codeの拡張機能など | `.devcontainer/devcontainer.json` |
| DinDで使うイメージ（Testcontainersなど） | `.devcontainer/images.txt`。[DockerとTestcontainers](docs/usage.md#dockerとtestcontainers)を参照 |
| proxyが通す接続先（`fonts.googleapis.com`など） | `.devcontainer/proxy.yaml`。[接続先を追加する](docs/network-policy.md#接続先を追加する)を参照 |
| 社内CAを使う接続先 | `.devcontainer/claude.env`か`.env`の`CLAUDE_DEVCONTAINER_PROXY_CA`。[社内CAを信頼させる](docs/network-policy.md#社内caを信頼させる)を参照 |
| claude.aiのコネクタ（Google Driveなど） | `.devcontainer/.env`の`CLAUDE_DEVCONTAINER_CONNECTORS`。[claude.aiのコネクタを使う](docs/network-policy.md#claudeaiのコネクタを使う)を参照 |

`agent-setup.sh`はホストでのビルド時に実行されるので、package registryに接続できます。agentの実行時はproxyを通るため接続できません。

`.devcontainer/compose.yaml`では`cap_add`、`privileged`、`networks`なども上書きできてしまいます。agentの隔離はこれらの設定に依存しているので、変更しないでください。

### ホストの設定を引き継ぐ

`devcontainer up`のたびに、設定のコピースクリプトがホストの`~/.claude`から次のものをagentへコピーします。

- `settings.json`、`CLAUDE.md`、`skills/`、`agents/`、`commands/`、`output-styles/`
- `settings.json`の`statusLine`と`hooks`のコマンドが`~/.claude/…`（`$HOME/.claude/…`でも可）として参照しているファイル

このスクリプトは、`.credentials.json`、セッション履歴、`plugins/`などをコピーしません。

このスクリプトは、`settings.json`をmanaged settings（`/etc/claude-code/managed-settings.d/50-host.json`）として配置し、ほかの`~/.claude`のファイルをroot所有でコピーします。
エージェントはこれらを書き換えられないので、ホストで設定したhookを外したり、hookが実行するスクリプトを差し替えたりできません。

ほかのファイルは、`~/.config/claude-devcontainer/extra/`にホームディレクトリと同じ構成で置くと、agentのホームディレクトリへコピーされます。
`extra/.claude/settings.json`を置けばホストの`settings.json`の代わりにそちらが使われます。
symlinkでも構いません。例:

```
cd ~/.config/claude-devcontainer/extra
mkdir -p .claude .config/git
ln -s ~/.claude/statusline.sh .claude/statusline.sh
ln -s ~/.config/git/config .config/git/config
```

設定のコピースクリプトは、Gitの設定を自動ではコピーしません。credential helper、署名、deltaのようなpagerなど、コンテナでは動かない設定が混ざりやすいからです。ホストの設定をそのまま渡すか、コンテナ用に書いたファイルを置くかを選んでください。このスクリプトは、symlinkで`~/.claude`ごと渡されても`extra/.claude/.credentials.json`をコピー対象から除外します。

> [!NOTE]
> - コピーはホストからコンテナへの一方向です。ホストの`settings.json`はmanaged settingsになるので、コンテナ内で`/config`などから変えた設定やプロジェクトの設定より優先されます。コンテナ用の値にしたい場合は、`extra/.claude/settings.json`を使ってください。
> - agentの`~/.claude`はコンテナの再起動で消えます（セッション履歴の`projects/`は残ります）。コピーしたファイルはroot所有なので、hookのスクリプトが自分の隣にファイルを書く場合は失敗します。
> - `settings.json`の`env`もそのままagentに見えます。秘密を書いている場合は、`extra/.claude/settings.json`でコンテナ用の設定を用意してください。
> - `/home/<user>/…`のような絶対パスを使ったコマンドはコンテナでは動きません。`~`か`$HOME`で書いてください。スクリプトが使うツールは`agent-setup.sh`にインストール手順を書いてください。
> - `up`の後にホストで設定を変えた場合は、`.devcontainer/claude/scripts/copy-host-config.py`を実行すると反映できます。

### 設定ファイル

設定は`.devcontainer/claude.env`と`.devcontainer/.env`のどちらにも書けます。リポジトリで共有するか、そのホストだけで使うかに応じて書き分けてください。

| ファイル | 設定する内容 | Gitでの扱い |
|---|---|---|
| `.devcontainer/claude.env` | DinD・nix・sumiの有効化など、リポジトリで共有する設定 | コミットする |
| `.devcontainer/.env` | そのホストでの機能の有効・無効、UID・ネットワーク・ファイルの場所など | `.gitignore`に追加し、コミットしない |

どちらも`KEY=value`形式で、ホスト側で編集します。agentからは書き換えられません。`.env`はホスト固有の設定が必要な場合だけ作成してください。

同じ項目を複数の場所に指定した場合は、起動時の環境変数、`.env`、`claude.env`の順に優先されます。たとえば、共有の`claude.env`でDinDを有効にしていても、手元の`.env`に`CLAUDE_DEVCONTAINER_DIND=0`と書けば、そのホストでは無効になります。空の値を指定した項目は既定値に戻り、優先順位の低いファイルの値は使われません。

DinD・nix・sumiは、いずれも既定では無効です。それぞれの設定値を`1`にすると有効、`0`にすると無効になります。設定を変更したら、ホストでリポジトリのルートからコンテナを作り直してください。

```sh
devcontainer up --remove-existing-container
```

### Dockerを使う（DinD）

[設定ファイル](#設定ファイル)に次の設定を書くことで、DinD (Docker in Docker)を有効にできます。

```dotenv
CLAUDE_DEVCONTAINER_DIND=1
```

DinDから外部のregistryには接続できないため、使うイメージを`.devcontainer/images.txt`に列挙する必要があります。
列挙されたイメージは、起動時にホストからDinDへ読み込まれます。
イメージの指定方法やポートへの接続方法は、[DockerとTestcontainers](docs/usage.md#dockerとtestcontainers)を参照してください。

### nixのdev shellを使う

ホストにnixがあり、リポジトリのflakeでdev shellを定義している場合は、そのツールをagentのBashでも使えます。flakeをコミットしてから、[設定ファイル](#設定ファイル)に次の設定を書いてください。

```dotenv
CLAUDE_DEVCONTAINER_NIX=1
```

既定では`HEAD`にあるflakeのdefault dev shellを使います。評価するコミットやdev shellを変える場合は、同じファイルに次の設定を追加してください。

```dotenv
# 評価するコミットやref。既定はHEAD
CLAUDE_DEVCONTAINER_NIX_REF=HEAD
# 名前付きdev shellを使う例。省略するとdefault
CLAUDE_DEVCONTAINER_NIX_SHELL=devShells.x86_64-linux.foo
```

`devcontainer up`のたびに、起動スクリプトがホストで`nix print-dev-env`を実行します。書き出した環境をagentのBashで読み込み、ホストの`/nix/store`を読み取り専用で使います。

> [!WARNING]
> * flakeの評価・inputの取得・ビルドはホストで実行され、proxyの通信制限を受けません。評価対象はコミット済みのflakeですが、agentもコミットやrefの変更ができます。`up`の前にflakeの変更を確認してください。
> * agentからはホストの`/nix/store`全体を読めます。store内の設定ファイルなどに秘密が含まれていれば、その値も見えます。

### シークレットをマスクする

作業ツリーの`.env`や`application.properties`にあるパスワードなどを、Claudeに見せたくない場合は[sumi](https://github.com/Hogeyama/nix-agent-sandbox/tree/main/contrib/sumi)を有効にしてください。`Read`や`Bash`などのツールの出力に含まれる指定した値を、`*`に置き換えてからClaudeへ渡します。既定では無効です。

まず、ホストの作業ツリーの外に、マスクしたい値の一覧を作成してください。既定の場所は`~/.claude/sumi/secrets.txt`です。

```sh
mkdir -p ~/.claude/sumi
[ -e ~/.claude/sumi/secrets.txt ] || install -m 600 /dev/null ~/.claude/sumi/secrets.txt
${EDITOR:-vi} ~/.claude/sumi/secrets.txt
chmod 600 ~/.claude/sumi/secrets.txt
```

ファイルの権限は0600にし、所有者のUIDをagentのUID（`CLAUDE_DEVCONTAINER_UID`、既定は1000）に合わせてください。ホストのユーザーと異なる場合は、[UIDの設定](#uidとネットワークを変える)も確認してください。

次に、[設定ファイル](#設定ファイル)に次の設定を書いてsumiを有効にします。

```dotenv
CLAUDE_DEVCONTAINER_SUMI=1
```

値の一覧を`~/.claude/sumi/secrets.txt`以外に置く場合は、ホストでの保存場所を`.devcontainer/.env`に指定してください。ディレクトリとファイル名を次の2項目に分けて書きます。

| 設定項目 | 指定するもの | 既定値 |
|---|---|---|
| `CLAUDE_DEVCONTAINER_SUMI_DIR` | 一覧ファイルを置いたホスト上のディレクトリ。相対パスは`.devcontainer/claude/`基準 | ホストの`~/.claude/sumi` |
| `CLAUDE_DEVCONTAINER_SUMI_FILE` | そのディレクトリ内のファイル名 | `secrets.txt` |

たとえば、ホストの`/home/me/secrets/sumi.txt`を読ませる場合は、次のように指定します。

```dotenv
CLAUDE_DEVCONTAINER_SUMI_DIR=/home/me/secrets
CLAUDE_DEVCONTAINER_SUMI_FILE=sumi.txt
```

（2変数に分けているのは単に実装の都合で、本質的な意味はありません）

sumiの起動とClaude Codeへのhookの設定は自動で行われます。ホストで`sumi init`を実行する必要はありません。

値の一覧は、ネットワークを持たない専用の`sumi`コンテナだけに読み取り専用で共有されます。agentには共有されず、ホストの設定をコピーする際にも除外されます。

一覧の内容を編集した場合は再起動不要で、次のツール実行から反映されます。実行中のコマンドには反映されません。空のファイルや不正な形式などで読み直せない場合は、それまでの一覧を使い続け、sumiのログに警告が出ます。

> [!WARNING]
> sumiはツールの出力をマスクする機能です。作業ツリーにある元のファイルをagentから隠すものではありません。`CLAUDE.md`のようにhookを通らずに読まれる内容や、二重のbase64など対応していない形式に変換された値は伏せられません。マスクの結果から値を推測されることも防げません。詳しくは[sumiのリミテーション](https://github.com/Hogeyama/nix-agent-sandbox/tree/main/contrib/sumi#リミテーション)を確認してください。

### UIDとネットワークを変える

`.devcontainer/.env`に以下の設定を書いてください。書かなかった項目は既定値になります。

```sh
CLAUDE_DEVCONTAINER_UID=1001             # agentのUID。既定は1000
CLAUDE_DEVCONTAINER_GID=1001             # agentのGID。既定は1000
CLAUDE_DEVCONTAINER_PROXY_NET=10.99.0    # agentとproxyの間のネットワーク。既定は10.231.70
CLAUDE_DEVCONTAINER_DIND_NET=10.99.1     # agentとDinDの間のネットワーク。既定は10.231.71
```

- UIDとGIDは、ホストでリポジトリを所有するユーザーに合わせてください。DinDのdockerdも同じUIDで動きます。UIDは0と、DinDが内部で使う100000〜165535は指定できません。
- ネットワークは`/24`の先頭3オクテットを書いてください。proxyは`.10`、DinDは`.10`、agentは`.20`を使います。同じホストで複数のリポジトリを同時に起動するときは、リポジトリごとに別の値にしてください。

UIDを変えた場合は、DinDのデータとClaudeのセッション履歴が古いUIDの所有のまま残り、DinDが起動できなくなります。先に[終了する](#6-終了する)の手順で`down --volumes`を実行してください（セッション履歴も消えます）。

## 更新する

```sh
git subtree pull --prefix=.devcontainer/claude https://github.com/Hogeyama/claude-devcontainer dist --squash
devcontainer up --remove-existing-container
```

`.devcontainer/claude/template/`が変わった場合は、自分の`devcontainer.json`などへ必要に応じて反映してください。
`claude.env`を使うには、雛形から同ファイルをコピーし、`compose.yaml`の`include`も雛形と同じ形にしてください。この2つは一緒に変更してください。

コアを直接編集した場合も、`git subtree pull`が上流の変更と3-way mergeするので、衝突しなければ手元の変更は残ります。ただし隔離にかかわる設定が上流の修正と混ざるため、できるだけ避けてください。

## このリポジトリを開発する

このリポジトリ自身も同じ構成で、`.devcontainer/claude`がコア、その外が自分のファイルです。`claude.env`でDinD・nix・sumiを有効にしています。`flake.nix`のdev shellにはPython、Node.js、Git、Docker CLI、OpenSSL、ShellCheckが入り、ホストでは`nix develop`、agentではBashから使えます。

sumiのデモ用の値を`.secrets/sumi/secrets.txt`にコミットしています。場所は`claude.env`に相対パスで設定済みで、起動時に権限を0600にします。`.secrets/`はagent側では空の読み取り専用領域で隠され、一覧はsumiコンテナだけに共有されます。

`.devcontainer/`直下のファイルは、取り込んだ後の状態を示す例です。たとえば`agent-setup.sh`はJDKとgradleを入れていますが、このリポジトリの開発には使いません。自分のリポジトリでは`.devcontainer/claude/template/`の雛形から始めてください。検証手順は[確認した範囲と検証手順](docs/testing.md)にあります。

`main`へpushすると、GitHub Actions（[`.github/workflows/dist.yml`](.github/workflows/dist.yml)）が[`scripts/build-dist.sh`](scripts/build-dist.sh)で`dist`ブランチにコミットを1つ足してpushします。中身は`main`の`.devcontainer/claude`と、それを最後に変更した`main`のコミットのURLを書いた`UPSTREAM`ファイルです。コアにもそのコミットにも変化がなければ何もしません。`dist`は先へ足すだけで書き換えないので、`main`をforce pushしても利用者の`git subtree pull`は壊れません。ただし、force pushで消えた`main`のコミットを指す古い`UPSTREAM`のリンクは、いずれ見られなくなります。`dist`は手で変更しないでください。
