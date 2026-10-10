# Claude Code用Dev Container

Claude Code用のDev Containerです。危険な操作を禁止しつつ、DinDなどの機能をエージェントに提供します。

自分のリポジトリにgit subtreeで取り込んで使います。取り込んだ部分（コア）は編集せず、ツールの追加などのカスタマイズはコアの外に置いた自分のファイルで行います。上流の修正は`git subtree pull`で取り込めます。

## ホストに必要なもの

- Linux x86_64
- Docker EngineとDocker Compose、およびDockerを操作できる権限
- Dev Containers CLI（`devcontainer`コマンド）
- git subtree（多くのディストリビューションのgitに同梱されています）
- Python 3
- privilegedコンテナ、ユーザー・ネットワークnamespace、bubblewrapを利用できるカーネル
- `10.231.70.0/24`と`10.231.71.0/24`がホスト・VPN・他コンテナと重複せず、ホストのfirewallがコンテナ間通信を許可する環境

## 利用上の制限

* 安全に利用するために、読み取り権限のみのGitHub fine-grained tokenを利用することを前提にしています。
* package registry、GitHubのrelease assetやLFS、Anthropicのweb searchやMCP connectorなどは使えません。
  * [通信ルールと隔離の範囲](docs/network-policy.md)に対応範囲を記載しています。
* UID 1000で読み書きできるリポジトリでしか使えません（今のところ）。

## クイックスタート

### 1. リポジトリに取り込む

使いたいリポジトリのルートで、コアを`.devcontainer/claude`に取り込み、雛形を`.devcontainer/`へコピーします。取り込み先は`.devcontainer/claude`に固定です。

```sh
git subtree add --prefix=.devcontainer/claude https://github.com/Hogeyama/claude-devcontainer dist --squash
cp .devcontainer/claude/template/{devcontainer.json,compose.yaml,agent-setup.sh} .devcontainer/
git add .devcontainer && git commit -m "Add Claude Code dev container"
```

`dist`ブランチは、このリポジトリの`.devcontainer/claude`だけを切り出したものです。取り込むと次の構成になります。

```
.devcontainer/
├── devcontainer.json   自分のファイル
├── compose.yaml        自分のファイル。claude/compose.yamlをincludeする
├── agent-setup.sh      自分のファイル。agentにツールを追加する
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

Dev Containersがimageのビルドと3つのコンテナ（本体、DinD、プロキシ）の起動を行います。
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

作業ファイル、DinDのデータ、プロキシのCAは残ります。これらも消す場合は`down --volumes`を使います。

## カスタマイズする

コアの外にある自分のファイルを編集します。

| 変えたいもの | 編集するファイル |
|---|---|
| agentのツール（言語処理系など） | `.devcontainer/agent-setup.sh`。imageのビルド時にrootで実行される |
| 環境変数・volumeなどcomposeの設定 | `.devcontainer/compose.yaml`の`services:`。相対パスは`.devcontainer/`が基準 |
| VS Codeの拡張機能など | `.devcontainer/devcontainer.json` |

`agent-setup.sh`はホストでのビルド時に実行されるので、package registryに接続できます。agentの実行時はproxyを通るため接続できません。

`.devcontainer/compose.yaml`では`cap_add`、`privileged`、`networks`なども上書きできてしまいます。agentの隔離はこれらの設定に依存しているので、変更しないでください。

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

コアを変更したら、`dist`ブランチを更新して公開します。

```sh
git subtree split --prefix=.devcontainer/claude -b dist
git push origin dist
```
