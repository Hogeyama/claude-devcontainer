# Claude Code用Dev Container

Claude Code用のDev Containerです。危険な操作を禁止しつつ、DinDなどの機能をエージェントに提供します。

自分のリポジトリにgit subtreeで取り込んで使います。取り込んだ部分（コア）は編集せず、ツールの追加などのカスタマイズはコアの外に置いた自分のファイルで行います。上流の修正は`git subtree pull`で取り込めます。

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
* Gitリポジトリのルートで使います。`git worktree`の作業ツリーには対応していません。
* agentは`.devcontainer/`と`.git`の設定（config、hooks）を書き換えられません。ただし、agentが作ったコミットに含まれる`.devcontainer/`の変更は、ホストで反映する前に確認してください。詳しくは[ホストが実行する設定の保護](docs/network-policy.md#ホストが実行する設定の保護)を参照してください。

## クイックスタート

### 1. リポジトリに取り込む

使いたいリポジトリのルートで、コアを`.devcontainer/claude`に取り込み、雛形を`.devcontainer/`へコピーします。取り込み先は`.devcontainer/claude`に固定です。

```sh
git subtree add --prefix=.devcontainer/claude https://github.com/Hogeyama/claude-devcontainer dist --squash
cp .devcontainer/claude/template/{devcontainer.json,compose.yaml,agent-setup.sh,images.txt} .devcontainer/
git add .devcontainer && git commit -m "Add Claude Code dev container"
```

`dist`ブランチは、このリポジトリの`.devcontainer/claude`だけを切り出したものです。取り込むと次の構成になります。

```
.devcontainer/
├── devcontainer.json   自分のファイル
├── compose.yaml        自分のファイル。claude/compose.yamlをincludeする
├── agent-setup.sh      自分のファイル。agentにツールを追加する
├── images.txt          自分のファイル。DinDに読み込むイメージ
└── claude/             コア。編集しない
```

### 2. 認証情報を用意する

#### GitHub

GitHubで[fine-grained PATを発行](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-fine-grained-personal-access-token)します。resource ownerを指定し、共有を許可した非公開リポジトリだけを対象にしてください。Contents、Issues、Pull requestsなど、作業に必要な権限だけを付けます。

#### Claude

Claudeのサブスクリプションを使う場合は、ホストのClaude Codeで[`setup-token`](https://code.claude.com/docs/en/cli-reference)を実行し、対話手順に従って長期トークンを発行します。

```sh
claude setup-token
```

API keyを使う場合は、代わりに[Claude ConsoleのAPI keys](https://platform.claude.com/settings/keys)で発行したkeyを使います。

### 3. 認証情報の取り出し方を設定する

ホストの`~/.config/claude-devcontainer/credentials`に、認証情報を取り出すシェルの代入を書きます。`GITHUB_TOKEN`と、`CLAUDE_CODE_OAUTH_TOKEN`または`ANTHROPIC_API_KEY`のどちらか一方を設定します。取り出し方は手元の環境に合わせて選んでください。

```sh
GITHUB_TOKEN=$(pass github/token/for-agent)                       # passを使う場合
CLAUDE_CODE_OAUTH_TOKEN=$(secret-tool lookup service claude-code) # libsecretを使う場合
# ANTHROPIC_API_KEY=$(op read op://Private/anthropic/credential)  # 1Password CLIを使う場合
# GITHUB_TOKEN=github_pat_...                                     # 値を直接書く場合。ファイルはchmod 600にしてください
```

このファイルはホストで実行されます。agentが書き換えられる作業ツリーの中には置かないでください。

### 4. 起動する

```sh
devcontainer up
```

Dev Containersがimageのビルドとコンテナ（本体とプロキシ。DinDを有効にした場合はDinDも）の起動を行います。
プロキシには3.で設定した認証情報が設定されます。

### 5. 実行する

CLIとして実行する場合:

```sh
devcontainer exec claude
```

VS Codeで実行する場合:

1. ホストのVS CodeにDev Containers拡張をインストールします。
2. 取り込んだリポジトリで`code .`を実行し、通知またはコマンドパレットから「Dev Containers: Reopen in Container」を実行します。上の手順で起動したコンテナにそのまま接続します。

VS Codeはホストの認証情報をコンテナへ転送する機能を持っています。使う前に[VS Codeで開く](docs/usage.md#vs-codeで開く)の利用条件を確認してください。

> [!NOTE]
> Claudeが接続できない場合は、[接続できない場合](docs/troubleshooting.md)を確認してください。

> [!NOTE]
> コマンドパレットから「Dev Containers: Install devcontainer CLI」を実行してCLIを落とすと、以降は `devcontainer open .` で直接 Dev Container を使った VS Code を開くことができます。
> npmやGitHubリリース経由でインストールできるdevcontainer CLIにはこの機能（`open`サブコマンド）がありません。

### 6. 終了する

ClaudeはCtrl-Cか`/exit`で終了します。VS Codeで開いていた場合は、ウィンドウを閉じるとComposeも停止します。

CLIで使っていた場合は、ホストで取り込んだリポジトリのルートで次を実行してComposeを停止します。

```sh
project=$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' \
  "$(docker ps -q --filter "label=devcontainer.local_folder=$(pwd -P)" --filter "label=com.docker.compose.service=agent")")
docker compose -p "$project" -f .devcontainer/compose.yaml down
```

作業ファイル、Claudeのセッション履歴（`~/.claude/projects`）、DinDのデータ、プロキシのCAは残ります。これらも消す場合は`down --volumes`を使います。

## カスタマイズする

コアの外にある自分のファイルを編集します。

| 変えたいもの | 編集するファイル |
|---|---|
| agentのツール（言語処理系など） | `.devcontainer/agent-setup.sh`。imageのビルド時にrootで実行される |
| 環境変数・volumeなどcomposeの設定 | `.devcontainer/compose.yaml`の`services:`。相対パスは`.devcontainer/`が基準 |
| VS Codeの拡張機能など | `.devcontainer/devcontainer.json` |
| DinDで使うイメージ（Testcontainersなど） | `.devcontainer/images.txt`。[DockerとTestcontainers](docs/usage.md#dockerとtestcontainers)を参照 |

`agent-setup.sh`はホストでのビルド時に実行されるので、package registryに接続できます。agentの実行時はproxyを通るため接続できません。

`.devcontainer/compose.yaml`では`cap_add`、`privileged`、`networks`なども上書きできてしまいます。agentの隔離はこれらの設定に依存しているので、変更しないでください。

### ホストの設定を引き継ぐ

`devcontainer up`のたびに、ホストの`~/.claude`から次のものがagentの`~/.claude`へコピーされます。

- `settings.json`、`CLAUDE.md`、`skills/`、`agents/`、`commands/`、`output-styles/`
- `settings.json`の`statusLine`と`hooks`のコマンドが`~/.claude/…`（`$HOME/.claude/…`でも可）として参照しているファイル

`.credentials.json`、セッション履歴、`plugins/`などはコピーしません。

`settings.json`はagentの`~/.claude`ではなく、managed settings（`/etc/claude-code/managed-settings.d/50-host.json`）として置きます。
ほかの`~/.claude`のファイルはroot所有でコピーします。
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

Gitの設定は自動ではコピーしません。credential helper、署名、deltaのようなpagerなど、コンテナでは動かない設定が混ざりやすいからです。ホストの設定をそのまま渡すか、コンテナ用に書いたファイルを置くかを選んでください。`extra/.claude/.credentials.json`はsymlinkで`~/.claude`ごと渡してもコピーしません。

> [!NOTE]
> - コピーはホストからコンテナへの一方向です。ホストの`settings.json`はmanaged settingsになるので、コンテナ内で`/config`などから変えた設定やプロジェクトの設定より優先されます。コンテナ用の値にしたい場合は、`extra/.claude/settings.json`を使ってください。
> - agentの`~/.claude`はコンテナの再起動で消えます（セッション履歴の`projects/`は残ります）。コピーしたファイルはroot所有なので、hookのスクリプトが自分の隣にファイルを書く場合は失敗します。
> - `settings.json`の`env`もそのままagentに見えます。秘密を書いている場合は、`extra/.claude/settings.json`でコンテナ用の設定を用意してください。
> - `/home/<user>/…`のような絶対パスを使ったコマンドはコンテナでは動きません。`~`か`$HOME`で書いてください。スクリプトが使うツールは`agent-setup.sh`で入れます。
> - `up`の後にホストで設定を変えた場合は、`.devcontainer/claude/scripts/copy-host-config.py`を実行すると反映できます。

### DinDとnixを使う

DinDとnixは、どちらも既定では無効です。必要な機能を`.devcontainer/.env`で`1`にすると有効になります。

```sh
# agentからDockerを使えるようにする
CLAUDE_DEVCONTAINER_DIND=1
# ホストの/nix/storeをmountし、flakeのdev shellを読み込む
CLAUDE_DEVCONTAINER_NIX=1
```

DinDを有効にすると、agentから`docker`やTestcontainersを使えます。使い方は[DockerとTestcontainers](docs/usage.md#dockerとtestcontainers)を参照してください。

nixを有効にすると、`devcontainer up`のたびにホストでリポジトリのflakeのdev shellを`nix print-dev-env`で書き出します。agentのbashはそれを読み込み、ホストの`/nix/store`を読み取り専用で使います。

```sh
# 評価するコミット。既定はHEAD
CLAUDE_DEVCONTAINER_NIX_REF=HEAD
# dev shellのattribute。既定はdefault
CLAUDE_DEVCONTAINER_NIX_SHELL=devShells.x86_64-linux.foo
```

> [!WARNING]
> - flakeの評価では、ホストでinputを取得し、ビルドも実行します。proxyを通らないので、agentがflakeを書き換えると、ホストに任意のURLへ接続させたり、ネットワークに接続できる状態でビルドさせたりできてしまいます。このため作業ツリーではなく、指定したコミットを評価します。ただし`.git`はagentが書き込めるので、agentがコミットしたりrefを動かしたりすると、それが評価されます。flakeに関わる変更は、`.devcontainer/`の変更と同じく、`up`の前に確認してください。不安な場合は、`CLAUDE_DEVCONTAINER_NIX_REF`に確認済みのコミットのハッシュを書きます。
> - ホストの`/nix/store`全体がagentから読めます。設定ファイルなどに秘密を入れてstoreに置いている場合は、それも見えます。

書き出したdev shellは`.devcontainer/.nix-env/`に置きます（Gitの管理対象からは自動で外れます）。

### UIDとネットワークを変える

`.devcontainer/.env`に書きます。書かなかった項目は既定値になります。

```sh
CLAUDE_DEVCONTAINER_UID=1001             # agentのUID。既定は1000
CLAUDE_DEVCONTAINER_GID=1001             # agentのGID。既定は1000
CLAUDE_DEVCONTAINER_PROXY_NET=10.99.0    # agentとproxyの間のネットワーク。既定は10.231.70
CLAUDE_DEVCONTAINER_DIND_NET=10.99.1     # agentとDinDの間のネットワーク。既定は10.231.71
```

- UIDとGIDは、ホストでリポジトリを所有するユーザーに合わせます（`id -u`と`id -g`で確認できます）。DinDのdockerdも同じUIDで動きます。UIDは0と、DinDが内部で使う100000〜165535は指定できません。
- ネットワークは`/24`の先頭3オクテットを書きます。proxyは`.10`、DinDは`.10`、agentは`.20`を使います。同じホストで複数のリポジトリを同時に起動するときは、リポジトリごとに別の値にしてください。

ホストごとに値が変わるので、`.env`はコミットせず`.gitignore`に入れておくとよいです。agentは`.devcontainer/`を書き換えられないので、`.env`も変更できません。

値を変えたら`devcontainer up --remove-existing-container`で作り直します。UIDを変えた場合は、DinDのデータとClaudeのセッション履歴が古いUIDの所有のまま残り、DinDが起動できなくなります。先に[終了する](#6-終了する)の手順で`down --volumes`を実行してください（セッション履歴も消えます）。

## 更新する

```sh
git subtree pull --prefix=.devcontainer/claude https://github.com/Hogeyama/claude-devcontainer dist --squash
devcontainer up --remove-existing-container
```

`.devcontainer/claude/template/`が変わった場合は、自分の`devcontainer.json`などへ必要に応じて反映してください。

コアを直接編集した場合も、`git subtree pull`が上流の変更と3-way mergeするので、衝突しなければ手元の変更は残ります。ただし隔離にかかわる設定が上流の修正と混ざるため、できるだけ避けてください。

## このリポジトリを開発する

このリポジトリ自身も同じ構成で、`.devcontainer/claude`がコア、その外が自分のファイルです。`devcontainer up`でそのまま開発環境として使えます。

`.devcontainer/`直下のファイルは、取り込んだ後の状態を示す例です。たとえば`agent-setup.sh`はJDKとgradleを入れていますが、このリポジトリの開発には使いません。自分のリポジトリでは`.devcontainer/claude/template/`の雛形から始めてください。検証手順は[確認した範囲と検証手順](docs/testing.md)にあります。

`main`へpushすると、GitHub Actions（[`.github/workflows/dist.yml`](.github/workflows/dist.yml)）が[`scripts/build-dist.sh`](scripts/build-dist.sh)で`dist`ブランチにコミットを1つ足してpushします。中身は`main`の`.devcontainer/claude`と、それを最後に変更した`main`のコミットのURLを書いた`UPSTREAM`ファイルです。コアにもそのコミットにも変化がなければ何もしません。`dist`は先へ足すだけで書き換えないので、`main`をforce pushしても利用者の`git subtree pull`は壊れません。ただし、force pushで消えた`main`のコミットを指す古い`UPSTREAM`のリンクは、いずれ見られなくなります。`dist`は手で変更しないでください。
